"""vh_server — FastAPI 薄壳:把 vh_core 内核暴露成 HTTP API,供 Web 工作台使用。

与 CLI 平级,不含业务逻辑;所有 job 经内存队列串行执行(单 GPU 机器,
模型跨 job 驻留),复用同一 Pipeline 实例,语义同 run_batch。

启动:uv run vh serve [--port 8100];或直接 uvicorn vh_server.main:app。
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from vh_core.api import build_pipeline, run_job
from vh_core.config import PROJECT_ROOT, Settings, load_settings
from vh_core.models import JobSpec
from vh_core.pipeline import Pipeline, PipelineStageError

STAGE_NAMES = ["download", "transcribe", "rewrite", "tts", "avatar", "postprocess", "publish"]

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}

settings: Settings = load_settings()
pipeline: Pipeline = build_pipeline(settings)

# ---------------------------------------------------------------- 任务队列

_job_q: queue.Queue[dict[str, Any]] = queue.Queue()
_states: dict[str, dict[str, Any]] = {}  # job_id -> {queue_status, error, submitted_at}
_states_lock = threading.Lock()


def _set_state(job_id: str, **kw: Any) -> None:
    with _states_lock:
        st = _states.setdefault(job_id, {})
        st.update(kw)


def _get_state(job_id: str) -> dict[str, Any]:
    with _states_lock:
        return dict(_states.get(job_id, {}))


def _job_worker() -> None:
    """串行消费任务队列:复用全局 pipeline,模型只加载一次。"""
    while True:
        item = _job_q.get()
        job_id = item["job_id"]
        _set_state(job_id, queue_status="running", error=None)
        try:
            run_job(
                JobSpec(job_id=job_id, params=item["params"]),
                settings=settings,
                pipeline=pipeline,
                only=item.get("only"),
                resume=item.get("resume", True),
            )
            _set_state(job_id, queue_status="done")
        except PipelineStageError as e:
            _set_state(job_id, queue_status="failed", error=f"stage '{e.stage_name}': {e.cause}")
        except Exception as e:
            logger.exception(f"[{job_id}] 队列任务失败")
            _set_state(job_id, queue_status="failed", error=str(e))
        finally:
            _job_q.task_done()


threading.Thread(target=_job_worker, daemon=True, name="vh-job-worker").start()


def _save_spec(job_id: str, params: dict[str, Any]) -> None:
    """服务端提交参数落盘,供重跑/详情页展示(CLI 建的 job 可能没有)。"""
    p = settings.workspace / job_id / "spec.server.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(params, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _load_spec(job_id: str) -> dict[str, Any]:
    p = settings.workspace / job_id / "spec.server.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


# 与创建任务同一条规则。\w 含中文等文字,再允许连字符。
_JOB_ID_RE = re.compile(r"[\w\-]+")


def _checked_job_dir(job_id: str) -> Path:
    """只接受 workspace 下面的一层任务目录,拒绝 ``..`` 逃出工作区。"""
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(404, f"任务不存在: {job_id}")
    root = settings.workspace.resolve()
    job_dir = (root / job_id).resolve()
    if job_dir == root or not job_dir.is_relative_to(root):
        raise HTTPException(404, f"任务不存在: {job_id}")
    return job_dir


def _file_inside(base: Path, target: Path) -> Path | None:
    """target 解析后必须是 base 里面的文件,否则返回 None。

    不能用字符串 startswith:目录名 ``demo`` 会误匹配旁边的 ``demo-secret``。
    """
    base_r = base.resolve()
    target_r = target.resolve()
    if target_r == base_r or not target_r.is_file() or not target_r.is_relative_to(base_r):
        return None
    return target_r


# ---------------------------------------------------------------- 请求模型


class JobCreate(BaseModel):
    job_id: str | None = None
    audio: str | None = None  # 直接驱动音频路径(跳过 TTS,直接对口型)
    text: str | None = None  # 口播文案(文案直给模式)或直接音频的配套字幕
    share_url: str | None = None  # 抖音分享链接(克隆模式)
    ref_audio: str | None = None
    ref_text: str | None = None
    avatar_video: str | None = None
    avatar_engine: str | None = None
    tts_engine: str | None = None
    tts: dict[str, Any] | None = None  # TTS 透传参数(本地引擎)
    avatar: dict[str, Any] | None = None  # 数字人透传参数(本地引擎)
    publish_platforms: list[str] | None = None
    publish_title: str | None = None
    publish_tags: list[str] | None = None
    postprocess: dict[str, Any] | None = None


class JobRun(BaseModel):
    resume: bool = True
    only: str | None = None  # 单 stage 名,如 publish
    params: dict[str, Any] | None = None  # 覆盖/补充参数(如发布平台)


# ---------------------------------------------------------------- FastAPI

app = FastAPI(title="videohuman 工作台", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 简单 token 鉴权:设置环境变量 VH_WEB_TOKEN 即启用(公网暴露时务必设置),
# /api/* 请求需带 X-VH-Token 头或 ?token= 查询参数(媒体标签无法设请求头)。
_WEB_TOKEN = os.environ.get("VH_WEB_TOKEN", "").strip()


@app.middleware("http")
async def token_auth(request, call_next):
    if _WEB_TOKEN and request.url.path.startswith("/api/"):
        token = request.headers.get("X-VH-Token") or request.query_params.get("token") or ""
        if token != _WEB_TOKEN:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "未授权:token 缺失或错误"}, status_code=401)
    return await call_next(request)


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    cookies_dir = settings.publish_cookies
    return {
        "tts_engines": ["cosyvoice3", "indextts2"],
        "default_tts_engine": settings.tts_engine,
        "avatar_engines": ["lstmsync", "musetalk"],
        "default_avatar_engine": settings.avatar_engine,
        "default_ref_audio": str(settings.default_ref_audio),
        "default_avatar_video": settings.avatar_video,
        "publish_platforms": ["douyin", "bilibili"],
        "llm_configured": bool(settings.llm_api_key),
        "douyin_cookie_exists": (cookies_dir / "douyin.json").exists(),
        "bilibili_cookie_exists": (cookies_dir / "bilibili.json").exists(),
        "stage_names": STAGE_NAMES,
    }


@app.get("/api/assets")
def list_assets() -> dict[str, Any]:
    assets = settings.assets

    def _rel(p: Path) -> str:
        return p.relative_to(PROJECT_ROOT).as_posix()

    audios = sorted(_rel(p) for p in assets.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    videos = sorted(_rel(p) for p in assets.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    bgm_dir = assets / "bgm"
    bgms = (
        sorted(_rel(p) for p in bgm_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
        if bgm_dir.is_dir()
        else []
    )
    return {"audios": audios, "videos": videos, "bgms": bgms}


@app.post("/api/assets")
async def upload_asset(file: UploadFile, kind: str = Query(..., pattern="^(audio|video|bgm)$")):
    ext = Path(file.filename or "").suffix.lower()
    allowed = AUDIO_EXTS if kind in ("audio", "bgm") else VIDEO_EXTS
    if ext not in allowed:
        raise HTTPException(400, f"不支持的文件类型 {ext}({kind} 允许: {sorted(allowed)})")
    dest_dir = settings.assets / "bgm" if kind == "bgm" else settings.assets
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(file.filename or f"upload{ext}").name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"ok": True, "path": dest.relative_to(PROJECT_ROOT).as_posix()}


@app.get("/api/asset-file")
def asset_file(path: str = Query(...)) -> FileResponse:
    """assets/ 内文件预览(音频试听等),限制在素材目录内。"""
    target = _file_inside(settings.assets, PROJECT_ROOT / path)
    if target is None:
        raise HTTPException(404, "文件不存在或路径非法")
    return FileResponse(target)


@app.get("/api/asset-ref-text")
def asset_ref_text(path: str = Query(...)) -> dict[str, Any]:
    """参考音文本:同名 .txt sidecar 直接读;没有就用 ASR 识别(限制在素材目录内)。"""
    from vh_core.providers import get_provider
    from vh_core.stages.tts import _clean_ref_text

    target = _file_inside(settings.assets, PROJECT_ROOT / path)
    if target is None:
        raise HTTPException(404, "文件不存在或路径非法")
    sidecar = target.with_suffix(".txt")
    if sidecar.exists():
        return {"text": sidecar.read_text(encoding="utf-8").strip(), "source": "sidecar"}
    text = _clean_ref_text(get_provider("asr", settings).transcribe(target).get("text") or "")
    if not text:
        raise HTTPException(422, "ASR 未能识别出文本,请检查音频内容")
    return {"text": text, "source": "asr"}


def _job_summary(job_dir: Path) -> dict[str, Any]:
    job_id = job_dir.name
    manifest_path = job_dir / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            # 损坏/半写入的 manifest 不应让任务列表整体 500,降级成空清单
            logger.warning(f"[{job_id}] manifest.json 读取失败,按空清单处理: {e}")
    stages = manifest.get("stages", {})
    state = _get_state(job_id)
    status = state.get("queue_status")
    if not status:
        if any(s.get("status") == "failed" for s in stages.values()):
            status = "failed"
        elif stages.get("postprocess", {}).get("status") in ("success", "skipped"):
            status = "done"
        elif stages:
            status = "interrupted"
        else:
            status = "unknown"
    final = stages.get("postprocess", {}).get("outputs", {}).get("final")
    spec = _load_spec(job_id)
    return {
        "job_id": job_id,
        "status": status,
        "error": state.get("error"),
        "created_at": manifest.get("created_at"),
        "mode": ("audio" if spec.get("audio") else ("clone" if spec.get("share_url") else "text")) if spec else None,
        "has_final": bool(final),
        "stages": {n: stages.get(n, {}).get("status", "pending") for n in STAGE_NAMES},
    }


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    ws = settings.workspace
    if not ws.is_dir():
        return []
    jobs = [_job_summary(d) for d in ws.iterdir() if d.is_dir()]
    jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return jobs


@app.post("/api/jobs")
def create_job(body: JobCreate) -> dict[str, Any]:
    has_audio = bool((body.audio or "").strip())
    has_text = bool((body.text or "").strip())
    has_share = bool((body.share_url or "").strip())

    if has_share and (has_audio or has_text):
        raise HTTPException(400, "抖音克隆模式(share_url)不能与 text 或 audio 同时提供")
    if not (has_audio or has_text or has_share):
        raise HTTPException(400, "必须提供 audio(直接音频)、text(文案直给) 或 share_url(抖音克隆) 其中之一")
    job_id = body.job_id or f"job-{datetime.now():%Y%m%d-%H%M%S}"
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(400, "job_id 只允许字母数字下划线连字符")
    params = body.model_dump(exclude_none=True, exclude={"job_id"})
    if body.publish_tags is not None:
        params["publish_tags"] = body.publish_tags
    _save_spec(job_id, params)
    _set_state(job_id, queue_status="queued", error=None, submitted_at=time.time())
    _job_q.put({"job_id": job_id, "params": params, "resume": True})
    return {"ok": True, "job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict[str, Any]:
    job_dir = _checked_job_dir(job_id)
    if not job_dir.is_dir():
        raise HTTPException(404, f"任务不存在: {job_id}")
    summary = _job_summary(job_dir)
    manifest_path = job_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    summary["manifest"] = manifest
    summary["spec"] = _load_spec(job_id)
    return summary


@app.post("/api/jobs/{job_id}/run")
def rerun_job(job_id: str, body: JobRun) -> dict[str, Any]:
    job_dir = _checked_job_dir(job_id)
    if not job_dir.is_dir():
        raise HTTPException(404, f"任务不存在: {job_id}")
    if body.only and body.only not in STAGE_NAMES:
        raise HTTPException(400, f"未知 stage: {body.only}(可选: {STAGE_NAMES})")
    params = _load_spec(job_id)
    params.update(body.params or {})
    if body.params:
        _save_spec(job_id, params)
    _set_state(job_id, queue_status="queued", error=None, submitted_at=time.time())
    _job_q.put({"job_id": job_id, "params": params, "only": [body.only] if body.only else None, "resume": body.resume})
    return {"ok": True, "job_id": job_id}


@app.get("/api/jobs/{job_id}/file")
def job_file(job_id: str, path: str = Query(...)) -> FileResponse:
    job_dir = _checked_job_dir(job_id)
    if not job_dir.is_dir():
        raise HTTPException(404, f"任务不存在: {job_id}")
    target = _file_inside(job_dir, job_dir / path)
    if target is None:
        raise HTTPException(404, "文件不存在或路径非法")
    return FileResponse(target)


# ---------------------------------------------------------------- 抖音登录

_login_state: dict[str, Any] = {"status": "idle", "error": None}


@app.post("/api/login/douyin")
def login_douyin_api() -> dict[str, Any]:
    if _login_state["status"] == "running":
        return {"ok": False, "status": "running", "detail": "登录流程进行中"}
    from vh_core.publishers.douyin import login_douyin

    cookies_path = settings.publish_cookies / "douyin.json"

    def _do_login() -> None:
        _login_state.update(status="running", error=None)
        try:
            login_douyin(cookies_path)
            _login_state.update(status="done")
        except Exception as e:
            logger.exception("抖音登录失败")
            _login_state.update(status="failed", error=str(e))

    threading.Thread(target=_do_login, daemon=True).start()
    return {"ok": True, "status": "running"}


@app.get("/api/login/douyin/status")
def login_douyin_status() -> dict[str, Any]:
    return {**_login_state, "cookie_exists": (settings.publish_cookies / "douyin.json").exists()}


# ---------------------------------------------------------------- 前端静态托管

_dist = PROJECT_ROOT / "web" / "dist"


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str) -> FileResponse:
    """SPA 托管:静态文件直出,其余路径回退 index.html(history 路由前端消化)。"""
    if not _dist.is_dir():
        raise HTTPException(404, "前端未构建:请先 cd web && npm run build")
    target = _file_inside(_dist, _dist / full_path) if full_path else None
    if target is not None:
        return FileResponse(target)
    return FileResponse(_dist / "index.html")
