"""服务端任务队列:单 worker 串行消费(单 GPU,模型跨 job 驻留),状态落盘。

- 队列状态写 workspace/{job_id}/status.server.json(原子写),服务重启后:
  queued 的任务按提交顺序自动重新入队;running 的任务标记为 interrupted(可续跑);
- 取消:排队中的任务直接出队;运行中的任务在下一个 stage 边界停下
  (GPU 推理无法安全中断);
- 同一 job 同时只允许一个排队/运行实例,重复提交抛 JobBusyError。
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from vh_core.pipeline import PipelineCancelled, PipelineStageError, atomic_write_text

STATUS_FILE = "status.server.json"
SPEC_FILE = "spec.server.json"

ACTIVE = frozenset({"queued", "running", "cancelling"})

# runner(job_id, params, only, resume, should_cancel) — 真正执行流水线的回调
Runner = Callable[[str, dict[str, Any], list[str] | None, bool, Callable[[], bool]], Any]


class JobBusyError(RuntimeError):
    """该 job 已在排队或运行中。"""


class JobQueue:
    def __init__(self, workspace: Path, runner: Runner):
        self.workspace = workspace
        self._runner = runner
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._pending: deque[str] = deque()  # 排队中的 job_id(FIFO)
        self._items: dict[str, dict[str, Any]] = {}  # job_id -> {params, only, resume}
        self._states: dict[str, dict[str, Any]] = {}
        self._cancel: set[str] = set()
        self._current: str | None = None
        self._stopping = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ 持久化

    def _status_path(self, job_id: str) -> Path:
        return self.workspace / job_id / STATUS_FILE

    def _persist(self, job_id: str) -> None:
        p = self._status_path(job_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, json.dumps(self._states[job_id], ensure_ascii=False, indent=2))

    def _load_state(self, job_id: str) -> dict[str, Any]:
        p = self._status_path(job_id)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[{job_id}] {STATUS_FILE} 读取失败,忽略: {e}")
            return {}

    def _update(self, job_id: str, **kw: Any) -> None:
        """调用方须持有 self._lock。"""
        st = self._states.setdefault(job_id, self._load_state(job_id))
        st.update(kw)
        self._persist(job_id)

    def save_spec(self, job_id: str, params: dict[str, Any]) -> None:
        p = self.workspace / job_id / SPEC_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, json.dumps(params, ensure_ascii=False, indent=2, default=str))

    def load_spec(self, job_id: str) -> dict[str, Any]:
        p = self.workspace / job_id / SPEC_FILE
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[{job_id}] {SPEC_FILE} 读取失败,按空参数处理: {e}")
            return {}

    # ------------------------------------------------------------ 对外接口

    def state(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            st = dict(self._states.get(job_id) or self._load_state(job_id))
            if st.get("queue_status") == "queued" and job_id in self._pending:
                st["queue_position"] = list(self._pending).index(job_id) + 1
            return st

    def is_active(self, job_id: str) -> bool:
        return self.state(job_id).get("queue_status") in ACTIVE

    def submit(self, job_id: str, params: dict[str, Any], only: list[str] | None = None, resume: bool = True) -> None:
        with self._cv:
            if job_id in self._pending or job_id == self._current:
                raise JobBusyError(f"任务 {job_id} 已在排队或运行中")
            self._items[job_id] = {"params": params, "only": only, "resume": resume}
            self._pending.append(job_id)
            self._cancel.discard(job_id)
            self._update(
                job_id,
                queue_status="queued",
                error=None,
                submitted_at=time.time(),
                started_at=None,
                finished_at=None,
                only=only,
                resume=resume,
            )
            self._cv.notify()

    def cancel(self, job_id: str) -> str:
        """返回取消后的状态:cancelled(已出队)/ cancelling(运行中,等 stage 边界)。"""
        with self._cv:
            if job_id in self._pending:
                self._pending.remove(job_id)
                self._items.pop(job_id, None)
                self._update(job_id, queue_status="cancelled", finished_at=time.time())
                return "cancelled"
            if job_id == self._current:
                self._cancel.add(job_id)
                self._update(job_id, queue_status="cancelling")
                return "cancelling"
        raise LookupError(f"任务 {job_id} 不在队列中")

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"running": self._current, "queued": len(self._pending)}

    # ------------------------------------------------------------ 生命周期

    def recover(self) -> int:
        """启动时扫描 workspace:queued 重新入队,running/cancelling 标记 interrupted。返回重新入队数。"""
        if not self.workspace.is_dir():
            return 0
        requeue: list[tuple[float, str, dict[str, Any]]] = []
        with self._lock:
            for d in self.workspace.iterdir():
                if not d.is_dir():
                    continue
                st = self._load_state(d.name)
                qs = st.get("queue_status")
                if qs in ("running", "cancelling"):
                    self._states[d.name] = st
                    self._update(
                        d.name,
                        queue_status="interrupted",
                        error="服务重启时任务被中断,可点击续跑从断点继续",
                        finished_at=time.time(),
                    )
                elif qs == "queued":
                    requeue.append((st.get("submitted_at") or 0.0, d.name, st))
        n = 0
        for _, job_id, st in sorted(requeue):
            try:
                self.submit(job_id, self.load_spec(job_id), only=st.get("only"), resume=st.get("resume", True))
                n += 1
            except JobBusyError:
                pass
        if n:
            logger.info(f"恢复 {n} 个重启前排队的任务")
        return n

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._worker, daemon=True, name="vh-job-worker")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._cv:
            self._stopping = True
            if self._current:
                self._cancel.add(self._current)
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout)

    def _worker(self) -> None:
        while True:
            with self._cv:
                while not self._pending and not self._stopping:
                    self._cv.wait()
                if self._stopping:
                    return
                job_id = self._pending.popleft()
                item = self._items.pop(job_id)
                self._current = job_id
                self._update(job_id, queue_status="running", error=None, started_at=time.time())
            status, error = "done", None
            try:
                self._runner(job_id, item["params"], item["only"], item["resume"], lambda j=job_id: j in self._cancel)
            except PipelineCancelled:
                status = "cancelled"
            except PipelineStageError as e:
                status, error = "failed", f"stage '{e.stage_name}': {e.cause}"
            except Exception as e:
                logger.exception(f"[{job_id}] 队列任务失败")
                status, error = "failed", str(e)
            with self._cv:
                self._current = None
                self._cancel.discard(job_id)
                self._update(job_id, queue_status=status, error=error, finished_at=time.time())
            logger.info(f"[{job_id}] 任务结束: {status}" + (f" ({error})" if error else ""))
