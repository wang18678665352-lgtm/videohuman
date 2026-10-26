"""Stage(步骤)抽象 + 流水线/工作流编排 + 断点续跑。

两种编排方式共用同一个执行器 Pipeline:
- 经典流水线:Pipeline(stages, settings) 按列表顺序执行,stage 之间按约定路径
  (如 tts/speech.wav)取上游产物,节点 id = stage.name;
- 工作流:Pipeline(nodes=..., ...)(由 vh_core.workflow 从 JSON 构建),节点声明
  输入/输出端口,上游产物经连线显式传入 ctx.inputs,节点可重复使用同一种 stage。

每个节点的产物落盘 workspace/{job_id}/{node_id}/ 并登记到 manifest.json,
按 manifest 判断已完成节点,支持断点续跑。
"""

from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger

from .config import Settings
from .models import JobManifest, StageResult, StageStatus, validate_job_id

# ---------------------------------------------------------------- 端口与参数描述

# 产物类型:端口连线时按类型校验
ARTIFACT_KINDS = ("text", "audio", "video", "image", "subtitle", "json")
# 目标端口可额外接受的源类型:视频可直接当音频源(ffmpeg/ASR 读取音轨)
_ACCEPTS = {"audio": {"video"}}


def kinds_compatible(src: str, dst: str) -> bool:
    return src == dst or src in _ACCEPTS.get(dst, ())


@dataclass(frozen=True)
class Port:
    kind: str
    title: str = ""
    required: bool = False


@dataclass(frozen=True)
class Param:
    """节点参数描述(供工作台生成表单)。key 可用点号表示嵌套,如 postprocess.subtitle。

    kind: string | text | number | integer | bool | select | asset_audio | asset_video | asset_dir
    """

    key: str
    title: str
    kind: str = "string"
    default: Any = None
    options: tuple[str, ...] = ()
    help: str = ""
    required: bool = False  # 新建任务时必须填写(工作台表单与服务端校验)


def expand_dotted(params: dict[str, Any]) -> dict[str, Any]:
    """{"postprocess.bgm": False} → {"postprocess": {"bgm": False}};已是嵌套的保持不变。"""
    out: dict[str, Any] = {}
    for key, val in params.items():
        parts = key.split(".")
        cur = out
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = cur[part] = {}
            cur = nxt
        if isinstance(val, dict) and isinstance(cur.get(parts[-1]), dict):
            cur[parts[-1]].update(val)
        else:
            cur[parts[-1]] = val
    return out


# ---------------------------------------------------------------- Stage


@dataclass
class StageContext:
    """传给每个 stage 的上下文：配置、job 信息、本节点的产物目录与输入。"""

    settings: Settings
    job_id: str
    params: dict[str, Any]
    stage_dir: Path
    job_dir: Path
    inputs: dict[str, Path] = field(default_factory=dict)
    legacy: bool = True  # 经典流水线:允许按约定路径回退取上游产物

    def rel(self, p: Path) -> str:
        """产物路径统一登记为相对 job 目录的 POSIX 形式。"""
        return p.resolve().relative_to(self.job_dir.resolve()).as_posix()

    def input(self, name: str, *legacy_paths: str) -> Path | None:
        """取输入端口的文件:连线传入优先;经典流水线下回退到约定路径(相对 job 目录)。"""
        p = self.inputs.get(name)
        if p is not None:
            return p
        if self.legacy:
            for rel in legacy_paths:
                cand = self.job_dir / rel
                if cand.exists():
                    return cand
        return None


class Stage(ABC):
    """步骤抽象：子类声明 name(类型 id)、端口与参数并实现 run()，返回 {输出端口名: 路径}。"""

    name: ClassVar[str]
    title: ClassVar[str] = ""
    category: ClassVar[str] = "处理"
    description: ClassVar[str] = ""
    inputs: ClassVar[dict[str, Port]] = {}
    outputs: ClassVar[dict[str, Port]] = {}
    params: ClassVar[tuple[Param, ...]] = ()

    def enabled(self, ctx: StageContext) -> bool:
        """本 job 是否需要执行该环节(如 download 无 share_url 时跳过)。"""
        return True

    @abstractmethod
    def run(self, ctx: StageContext) -> dict[str, Path]:
        """执行本环节，产物必须写在 ctx.stage_dir 下并返回。失败抛异常。"""


@dataclass
class Node:
    """工作流中的一个节点:stage 实例 + 节点参数 + 输入连线 {输入端口: (上游节点 id, 输出端口)}。

    params 为 None 表示经典流水线节点,直接使用 job 级参数。
    """

    id: str
    stage: Stage
    params: dict[str, Any] | None = None
    inputs: dict[str, tuple[str, str]] = field(default_factory=dict)


class Pipeline:
    def __init__(
        self, stages: list[Stage] | None = None, settings: Settings | None = None, nodes: list[Node] | None = None
    ):
        if settings is None:
            raise ValueError("Pipeline 需要 settings")
        self.settings = settings
        self.legacy = nodes is None
        self.nodes = nodes if nodes is not None else [Node(id=s.name, stage=s) for s in (stages or [])]

    @property
    def stages(self) -> list[Stage]:
        return [n.stage for n in self.nodes]

    def _job_dir(self, job_id: str) -> Path:
        return self.settings.workspace / validate_job_id(job_id)

    def _manifest_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "manifest.json"

    def load_manifest(self, job_id: str) -> JobManifest:
        p = self._manifest_path(job_id)
        if p.exists():
            return JobManifest.model_validate(json.loads(p.read_text(encoding="utf-8")))
        return JobManifest(job_id=job_id)

    def save_manifest(self, manifest: JobManifest) -> None:
        p = self._manifest_path(manifest.job_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, manifest.model_dump_json(indent=2))

    def _resolve_inputs(self, node: Node, manifest: JobManifest, job_dir: Path) -> dict[str, Path] | None:
        """按连线从 manifest 取上游产物。必填输入的上游被跳过时返回 None(本节点随之跳过)。"""
        resolved: dict[str, Path] = {}
        for port, (src_id, src_port) in node.inputs.items():
            required = node.stage.inputs.get(port, Port("")).required
            src = manifest.stages.get(src_id)
            if src is None:
                if required:
                    raise RuntimeError(f"输入 '{port}' 的上游节点 '{src_id}' 尚未运行")
                continue
            if src.status == StageStatus.SKIPPED and not src.outputs:
                if required:
                    return None
                continue
            rel = src.outputs.get(src_port)
            if rel is None:
                if required:
                    raise RuntimeError(
                        f"上游节点 '{src_id}' 没有产出 '{src_port}'(节点 '{node.id}' 的输入 '{port}' 必需)"
                    )
                continue
            resolved[port] = job_dir / rel
        return resolved

    def run(
        self,
        job_id: str,
        params: dict[str, Any] | None = None,
        only: list[str] | None = None,
        resume: bool = True,
        should_cancel: Callable[[], bool] | None = None,
    ) -> JobManifest:
        """按序执行节点。resume=True 时跳过 manifest 中已成功的节点。

        only:只跑指定节点,且**无论是否已完成都重跑**(单步重跑语义),
        其余节点的 manifest 记录保留;resume=False 仅在整跑时清空 manifest。
        should_cancel:每个节点开始前调用,返回 True 则抛 PipelineCancelled
        (GPU 推理无法安全中断,取消在节点边界生效)。
        """
        params = params or {}
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        fresh = not resume and only is None
        manifest = JobManifest(job_id=job_id) if fresh else self.load_manifest(job_id)

        for node in self.nodes:
            nid = node.id
            if only is not None and nid not in only:
                continue
            if only is None and manifest.is_done(nid):
                # 不改写状态:保持 SUCCESS,否则下一次续跑会把它当未完成重跑
                logger.info(f"[{job_id}] 节点 '{nid}' 已完成，跳过(断点续跑)")
                continue
            if should_cancel is not None and should_cancel():
                logger.info(f"[{job_id}] 已取消,停在节点 '{nid}' 之前")
                raise PipelineCancelled(job_id, nid)

            stage_dir = job_dir / nid
            stage_dir.mkdir(parents=True, exist_ok=True)
            try:
                inputs = self._resolve_inputs(node, manifest, job_dir)
            except RuntimeError as e:
                manifest.stages[nid] = StageResult(name=nid, status=StageStatus.FAILED, error=str(e))
                self.save_manifest(manifest)
                raise PipelineStageError(nid, e) from e
            ctx = StageContext(
                settings=self.settings,
                job_id=job_id,
                params=params if node.params is None else node.params,
                stage_dir=stage_dir,
                job_dir=job_dir,
                inputs=inputs or {},
                legacy=self.legacy,
            )
            if inputs is None or not node.stage.enabled(ctx):
                logger.info(f"[{job_id}] 节点 '{nid}' 本 job 不适用,跳过")
                manifest.stages[nid] = StageResult(name=nid, status=StageStatus.SKIPPED)
                self.save_manifest(manifest)
                continue
            result = StageResult(
                name=nid,
                status=StageStatus.RUNNING,
                started_at=datetime.now(UTC),
            )
            manifest.stages[nid] = result
            self.save_manifest(manifest)  # RUNNING 也落盘，崩溃后可见现场

            try:
                outputs = node.stage.run(ctx)
            except Exception as e:
                logger.exception(f"[{job_id}] 节点 '{nid}' 失败")
                result.status = StageStatus.FAILED
                result.error = str(e)
                result.finished_at = datetime.now(UTC)
                self.save_manifest(manifest)
                raise PipelineStageError(nid, e) from e

            result.status = StageStatus.SUCCESS
            result.outputs = {k: ctx.rel(v) for k, v in outputs.items()}
            result.finished_at = datetime.now(UTC)
            self.save_manifest(manifest)
            logger.info(f"[{job_id}] 节点 '{nid}' 完成: {result.outputs}")

        return manifest


def atomic_write_text(path: Path, text: str) -> None:
    """先写同目录临时文件再 os.replace:进程崩溃/断电也不会留下半截 JSON。"""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class PipelineCancelled(RuntimeError):
    def __init__(self, job_id: str, stage_name: str):
        super().__init__(f"任务 {job_id} 已取消(停在 stage '{stage_name}' 之前)")
        self.job_id = job_id
        self.stage_name = stage_name


class PipelineStageError(RuntimeError):
    def __init__(self, stage_name: str, cause: Exception):
        super().__init__(f"stage '{stage_name}' 执行失败: {cause}")
        self.stage_name = stage_name
        self.cause = cause
