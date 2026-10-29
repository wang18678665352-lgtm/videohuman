"""vh_server — FastAPI 薄壳:把 vh_core 内核暴露成 HTTP API,供 Web 工作台使用。

与 CLI 平级,不含业务逻辑;所有 job 经 JobQueue 串行执行(单 GPU 机器,
模型跨 job 驻留),复用同一 Pipeline 实例,语义同 run_batch。

启动:uv run vh serve [--port 8100];或直接 uvicorn vh_server.main:app。
测试/嵌入:create_app(settings, runner=...) 可注入假 runner,不加载任何模型。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from vh_core import __version__
from vh_core import workflow as wf
from vh_core.config import PROJECT_ROOT, Settings, load_settings
from vh_core.models import STAGE_NAMES, JobSpec, validate_job_id
from vh_core.pipeline import expand_dotted

from .jobs import ACTIVE, JobBusyError, JobQueue, Runner

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
# 上传类型 → (允许的扩展名, 素材库内子目录)
UPLOAD_KINDS = {
    "audio": (AUDIO_EXTS, ""),
    "video": (VIDEO_EXTS, ""),
    "bgm": (AUDIO_EXTS, "bgm"),
    "broll": (IMAGE_EXTS | VIDEO_EXTS, "broll"),
}

# 客户端可提交的"文件路径"参数:服务端只允许指向 assets/ 或 workspace/ 内,
# 防止经 API 让 ffmpeg/ASR 读取服务器任意文件再经产物下载外泄
PATH_PARAMS = (
    "audio",
    "ref_audio",
    "avatar_video",
    "bgm_path",
    "subtitle_font",
    "transcribe_audio",
    "path",
    "media_dir",
)

# 鉴权失败限速:同一 IP 在窗口期内失败次数超限即临时封禁
AUTH_FAIL_WINDOW_S = 300
AUTH_FAIL_MAX = 20

# 上传素材的授权声明(前端原文展示,服务端随上传记录存档于 assets/.consent.jsonl)
CONSENT_STATEMENTS = {
    "audio": "我确认:该音频中的声音属于本人,或已取得声音权利人的书面授权,同意将其用于 AI 声音合成。",
    "video": "我确认:该视频中的人物肖像属于本人,或已取得肖像权利人的书面授权,同意将其用于 AI 数字人生成。",
    "bgm": "我确认:已取得该音乐的合法使用授权(含商用场景,如适用)。",
    "broll": "我确认:已取得该图片/视频素材的使用授权;画面中如有可识别人物,已取得其肖像授权。",
}
CONSENT_LOG = ".consent.jsonl"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
}


# ---------------------------------------------------------------- 请求模型


class JobCreate(BaseModel):
    job_id: str | None = Field(None, max_length=128)
    # 工作流模式:workflow = 模板/工作流 id,node_params = {节点 id: {参数: 值}};给了 workflow 时忽略下方经典参数
    workflow: str | None = Field(None, max_length=64)
    node_params: dict[str, dict[str, Any]] | None = None
    audio: str | None = None  # 直接驱动音频路径(跳过 TTS,直接对口型)
    text: str | None = Field(None, max_length=20000)  # 口播文案(文案直给模式)或直接音频的配套字幕
    share_url: str | None = Field(None, max_length=2000)  # 抖音分享链接(克隆模式)
    ref_audio: str | None = None
    ref_text: str | None = Field(None, max_length=2000)
    avatar_video: str | None = None
    avatar_engine: str | None = None
    tts_engine: str | None = None
    tts: dict[str, Any] | None = None  # TTS 透传参数(本地引擎)
    avatar: dict[str, Any] | None = None  # 数字人透传参数(本地引擎)
    publish_platforms: list[str] | None = None
    publish_title: str | None = Field(None, max_length=200)
    publish_tags: list[str] | None = None
    publish_date: str | None = None
    postprocess: dict[str, Any] | None = None


class JobRun(BaseModel):
    resume: bool = True
    only: str | None = None  # 单 stage / 工作流节点 id,如 publish
    params: dict[str, Any] | None = None  # 覆盖/补充参数(如发布平台;工作流任务用 {"node_params": {...}})


# ---------------------------------------------------------------- 工具函数


def _rel(p: Path) -> str:
    """返回给前端的路径:项目内用相对路径,项目外(如卷挂载的素材目录)用绝对路径。"""
    try:
        return p.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def _within(target: Path, *bases: Path) -> bool:
    return any(target.is_relative_to(b.resolve()) for b in bases)


def _check_param_paths(settings: Settings, params: dict[str, Any]) -> None:
    allowed = (settings.assets, settings.workspace)
    candidates = [(k, params.get(k)) for k in PATH_PARAMS]
    pp = params.get("postprocess")
    if isinstance(pp, dict) and isinstance(pp.get("bg_image"), str):
        # bg_image 允许只写 background_images/ 下的文件名
        bg = pp["bg_image"]
        if "/" in bg or "\\" in bg:
            candidates.append(("postprocess.bg_image", bg))
    for key, val in candidates:
        if val is None or val == "":
            continue
        if not isinstance(val, str):
            raise HTTPException(400, f"参数 {key} 必须是字符串路径")
        target = settings.abs_path(Path(val)).resolve()
        if not _within(target, *allowed):
            raise HTTPException(400, f"参数 {key} 只允许引用 assets/ 或 workspace/ 内的文件: {val}")


def _check_workflow_paths(settings: Settings, spec: wf.WorkflowSpec, node_params: dict[str, Any] | None) -> None:
    for node_id, params in wf.effective_params(spec, node_params).items():
        try:
            _check_param_paths(settings, expand_dotted(params))
        except HTTPException as e:
            raise HTTPException(400, f"节点 {node_id}: {e.detail}") from e


def _merge_node_params(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    merged = {k: dict(v) for k, v in (base or {}).items()}
    for node_id, params in (extra or {}).items():
        merged.setdefault(node_id, {}).update(params or {})
    return merged


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    for i in range(1, 10_000):
        cand = dest.with_name(f"{dest.stem}_{i}{dest.suffix}")
        if not cand.exists():
            return cand
    raise HTTPException(409, "同名文件过多,请改名后上传")


def _safe_filename(name: str, ext: str) -> str:
    stem = Path(name.replace("\\", "/")).name
    stem = Path(stem).stem.strip().lstrip(".")
    stem = "".join(c for c in stem if c.isalnum() or c in "-_ ()").strip()[:100]
    return f"{stem or 'upload'}{ext}"


def default_runner(settings: Settings) -> Runner:
    from vh_core.api import build_pipeline, run_job

    pipeline = build_pipeline(settings)

    def _run(job_id, params, only, resume, should_cancel):
        return run_job(
            JobSpec(job_id=job_id, params=params),
            settings=settings,
            pipeline=pipeline,
            only=only,
            resume=resume,
            should_cancel=should_cancel,
        )

    return _run


# ---------------------------------------------------------------- 应用工厂


def create_app(settings: Settings | None = None, runner: Runner | None = None) -> FastAPI:
    settings = settings or load_settings()
    queue = JobQueue(settings.workspace, runner or default_runner(settings))
    token = settings.web_token.strip()
    auth_fails: dict[str, list[float]] = {}
    auth_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        settings.workspace.mkdir(parents=True, exist_ok=True)
        queue.start()
        queue.recover()
        logger.info(f"videohuman 服务启动 v{__version__},鉴权={'开启' if token else '关闭'}")
        yield
        queue.stop()

    app = FastAPI(title="videohuman 工作台", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.queue = queue

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _auth_blocked(ip: str, now: float) -> bool:
        with auth_lock:
            fails = [t for t in auth_fails.get(ip, []) if now - t < AUTH_FAIL_WINDOW_S]
            auth_fails[ip] = fails
            return len(fails) >= AUTH_FAIL_MAX

    def _auth_fail(ip: str, now: float) -> None:
        with auth_lock:
            auth_fails.setdefault(ip, []).append(now)

    @app.middleware("http")
    async def access_and_auth(request: Request, call_next: Callable):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        path = request.url.path
        # /api/health 供负载均衡/容器探活,不需要鉴权
        if token and path.startswith("/api/") and path != "/api/health":
            ip = request.client.host if request.client else "-"
            now = time.time()
            if _auth_blocked(ip, now):
                return JSONResponse({"detail": "鉴权失败次数过多,请稍后再试"}, status_code=429)
            # X-VH-Token 头优先;媒体标签无法设请求头,允许 ?token= 查询参数
            given = request.headers.get("X-VH-Token") or request.query_params.get("token") or ""
            if not hmac.compare_digest(given.encode(), token.encode()):
                _auth_fail(ip, now)
                return JSONResponse({"detail": "未授权:token 缺失或错误"}, status_code=401)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(f"[{rid}] {request.method} {path} 未处理异常")
            response = JSONResponse({"detail": "服务器内部错误", "request_id": rid}, status_code=500)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        response.headers["X-Request-ID"] = rid
        if path.startswith("/api/"):
            ms = (time.perf_counter() - start) * 1000
            logger.debug(f"[{rid}] {request.method} {path} -> {response.status_code} {ms:.0f}ms")
        return response

    # ------------------------------------------------------------ 系统

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "queue": queue.stats()}

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        cookies_dir = settings.publish_cookies
        return {
            "version": __version__,
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
            "stage_names": list(STAGE_NAMES),
            "max_upload_mb": settings.max_upload_mb,
            "consent_statements": CONSENT_STATEMENTS,
            "commercial_mode": bool(settings.commercial_mode),
        }

    # ------------------------------------------------------------ 素材

    def _asset_target(path: str) -> Path:
        base = settings.assets.resolve()
        target = (PROJECT_ROOT / path).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise HTTPException(404, "文件不存在或路径非法")
        return target

    @app.get("/api/assets")
    def list_assets() -> dict[str, Any]:
        assets = settings.assets
        if not assets.is_dir():
            return {"audios": [], "videos": [], "bgms": [], "brolls": [], "dirs": []}

        def _scan(d: Path, exts: set[str]) -> list[str]:
            if not d.is_dir():
                return []
            return sorted(_rel(p) for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts)

        return {
            "audios": _scan(assets, AUDIO_EXTS),
            "videos": _scan(assets, VIDEO_EXTS),
            "bgms": _scan(assets / "bgm", AUDIO_EXTS),
            "brolls": _scan(assets / "broll", IMAGE_EXTS | VIDEO_EXTS),
            # 素材库下的子目录(混剪节点可选不同素材目录)
            "dirs": sorted(_rel(d) for d in assets.iterdir() if d.is_dir() and not d.name.startswith(".")),
        }

    @app.post("/api/assets")
    def upload_asset(
        request: Request,
        file: UploadFile,
        kind: str = Query(..., pattern="^(audio|video|bgm|broll)$"),
        consent: bool = Query(False, description="上传者确认已获得授权(见 /api/settings 的 consent_statements)"),
    ) -> dict[str, Any]:
        if not consent:
            raise HTTPException(400, f"上传前需确认授权声明:{CONSENT_STATEMENTS[kind]}")
        ext = Path(file.filename or "").suffix.lower()
        allowed, subdir = UPLOAD_KINDS[kind]
        if ext not in allowed:
            raise HTTPException(400, f"不支持的文件类型 {ext}({kind} 允许: {sorted(allowed)})")
        dest_dir = settings.assets / subdir if subdir else settings.assets
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique_dest(dest_dir / _safe_filename(file.filename or "", ext))
        limit = settings.max_upload_mb * 1024 * 1024
        tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.part")
        written = 0
        digest = hashlib.sha256()
        try:
            with tmp.open("wb") as f:
                while chunk := file.file.read(1 << 20):
                    written += len(chunk)
                    digest.update(chunk)
                    if written > limit:
                        raise HTTPException(413, f"文件超过上限 {settings.max_upload_mb} MB")
                    f.write(chunk)
            if written == 0:
                raise HTTPException(400, "空文件")
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        # 授权存档:谁(IP)、何时、对哪个文件(sha256 防替换)确认了哪条声明
        record = {
            "file": _rel(dest),
            "kind": kind,
            "sha256": digest.hexdigest(),
            "size": written,
            "statement": CONSENT_STATEMENTS[kind],
            "client_ip": request.client.host if request.client else None,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        with (settings.assets / CONSENT_LOG).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info(f"素材上传: {dest.name} ({written / 1e6:.1f} MB),授权声明已存档")
        return {"ok": True, "path": _rel(dest)}

    @app.get("/api/asset-file")
    def asset_file(path: str = Query(...)) -> FileResponse:
        """assets/ 内文件预览(音频试听等),限制在素材目录内。"""
        return FileResponse(_asset_target(path))

    @app.get("/api/asset-ref-text")
    def asset_ref_text(path: str = Query(...)) -> dict[str, Any]:
        """参考音文本:同名 .txt sidecar 直接读;没有就用 ASR 识别(限制在素材目录内)。"""
        from vh_core.providers import get_provider
        from vh_core.stages.tts import _clean_ref_text

        target = _asset_target(path)
        sidecar = target.with_suffix(".txt")
        if sidecar.exists():
            return {"text": sidecar.read_text(encoding="utf-8").strip(), "source": "sidecar"}
        text = _clean_ref_text(get_provider("asr", settings).transcribe(target).get("text") or "")
        if not text:
            raise HTTPException(422, "ASR 未能识别出文本,请检查音频内容")
        return {"text": text, "source": "asr"}

    # ------------------------------------------------------------ 任务

    def _job_dir(job_id: str) -> Path:
        try:
            validate_job_id(job_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        job_dir = settings.workspace / job_id
        if not job_dir.is_dir():
            raise HTTPException(404, f"任务不存在: {job_id}")
        return job_dir

    def _read_manifest(job_dir: Path) -> dict[str, Any]:
        p = job_dir / "manifest.json"
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            # 损坏/半写入的 manifest 不应让任务列表整体 500,降级成空清单
            logger.warning(f"[{job_dir.name}] manifest.json 读取失败,按空清单处理: {e}")
            return {}

    def _job_summary(job_dir: Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        job_id = job_dir.name
        manifest = _read_manifest(job_dir) if manifest is None else manifest
        stages = manifest.get("stages", {})
        spec = queue.load_spec(job_id)
        flow = wf.load_job_workflow(job_dir)
        if flow is not None:
            order = [n.id for n in wf.topo_order(flow)]
            final_node, _, final_port = (wf.final_ref(flow) or ".").partition(".")
            mode = flow.id
        else:
            order = list(STAGE_NAMES)
            final_node, final_port = "postprocess", "final"
            mode = ("audio" if spec.get("audio") else ("clone" if spec.get("share_url") else "text")) if spec else None
        final = stages.get(final_node, {}).get("outputs", {}).get(final_port)
        cover = stages.get(final_node, {}).get("outputs", {}).get("cover")  # 后期节点同时产出封面
        state = queue.state(job_id)
        status = state.get("queue_status")
        if not status:
            if any(s.get("status") == "failed" for s in stages.values()):
                status = "failed"
            elif stages.get(final_node, {}).get("status") in ("success", "skipped"):
                status = "done"
            elif stages:
                status = "interrupted"
            else:
                status = "unknown"
        return {
            "job_id": job_id,
            "status": status,
            "error": state.get("error"),
            "queue_position": state.get("queue_position"),
            "created_at": manifest.get("created_at")
            or (
                datetime.fromtimestamp(state["submitted_at"]).astimezone().isoformat()
                if state.get("submitted_at")
                else None
            ),
            "mode": mode,
            "workflow": {"id": flow.id, "title": flow.title} if flow else None,
            "has_final": bool(final),
            "final": final,
            "cover": cover,
            "stage_order": order,
            "stages": {n: stages.get(n, {}).get("status", "pending") for n in order},
        }

    @app.get("/api/jobs")
    def list_jobs(
        status: str | None = Query(None, description="按状态过滤"),
        limit: int = Query(200, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        ws = settings.workspace
        if not ws.is_dir():
            return []
        jobs = [_job_summary(d) for d in ws.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if status:
            jobs = [j for j in jobs if j["status"] == status]
        jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
        return jobs[offset : offset + limit]

    @app.post("/api/jobs")
    def create_job(body: JobCreate) -> dict[str, Any]:
        if body.workflow:
            return _create_workflow_job(body)
        has_audio = bool((body.audio or "").strip())
        has_text = bool((body.text or "").strip())
        has_share = bool((body.share_url or "").strip())

        if has_share and (has_audio or has_text):
            raise HTTPException(400, "抖音克隆模式(share_url)不能与 text 或 audio 同时提供")
        if not (has_audio or has_text or has_share):
            raise HTTPException(400, "必须提供 audio(直接音频)、text(文案直给) 或 share_url(抖音克隆) 其中之一")
        job_id = _new_job_id(body)
        params = body.model_dump(exclude_none=True, exclude={"job_id", "workflow", "node_params"})
        _check_param_paths(settings, params)
        queue.save_spec(job_id, params)
        try:
            queue.submit(job_id, params, resume=True)
        except JobBusyError as e:
            raise HTTPException(409, str(e)) from e
        logger.info(f"[{job_id}] 新建任务入队")
        return {"ok": True, "job_id": job_id}

    def _new_job_id(body: JobCreate) -> str:
        job_id = body.job_id or f"job-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        try:
            validate_job_id(job_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if (settings.workspace / job_id).exists():
            raise HTTPException(409, f"任务 {job_id} 已存在,请换一个 ID 或在详情页重跑")
        return job_id

    def _create_workflow_job(body: JobCreate) -> dict[str, Any]:
        try:
            spec = wf.get_workflow(settings, body.workflow or "")
        except KeyError as e:
            raise HTTPException(404, f"工作流不存在: {body.workflow}") from e
        errors = wf.validate_workflow(spec) + wf.missing_required_params(spec, body.node_params)
        if errors:
            raise HTTPException(400, ";".join(errors))
        _check_workflow_paths(settings, spec, body.node_params)
        job_id = _new_job_id(body)
        # 立即写工作流快照:之后修改模板不影响本任务(含排队期间)
        wf.resolve_job_workflow(settings, settings.workspace / job_id, spec.model_dump())
        params = {"workflow": spec.id, "node_params": body.node_params or {}}
        queue.save_spec(job_id, params)
        try:
            queue.submit(job_id, params, resume=True)
        except JobBusyError as e:
            raise HTTPException(409, str(e)) from e
        logger.info(f"[{job_id}] 新建工作流任务入队: {spec.id}")
        return {"ok": True, "job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> dict[str, Any]:
        job_dir = _job_dir(job_id)
        manifest = _read_manifest(job_dir)
        summary = _job_summary(job_dir, manifest)
        summary["manifest"] = manifest
        summary["spec"] = queue.load_spec(job_id)
        return summary

    @app.post("/api/jobs/{job_id}/run")
    def rerun_job(job_id: str, body: JobRun) -> dict[str, Any]:
        job_dir = _job_dir(job_id)
        flow = wf.load_job_workflow(job_dir)
        names = [n.id for n in flow.nodes] if flow else list(STAGE_NAMES)
        if body.only and body.only not in names:
            raise HTTPException(400, f"未知环节: {body.only}(可选: {names})")
        params = queue.load_spec(job_id)
        if flow is not None:
            extra = (body.params or {}).get("node_params")
            params["node_params"] = _merge_node_params(params.get("node_params"), extra)
            _check_workflow_paths(settings, flow, params["node_params"])
        else:
            params.update(body.params or {})
            _check_param_paths(settings, params)
        if body.params:
            queue.save_spec(job_id, params)
        try:
            queue.submit(job_id, params, only=[body.only] if body.only else None, resume=body.resume)
        except JobBusyError as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True, "job_id": job_id}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        _job_dir(job_id)
        try:
            status = queue.cancel(job_id)
        except LookupError as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True, "job_id": job_id, "status": status}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict[str, Any]:
        job_dir = _job_dir(job_id)
        if queue.state(job_id).get("queue_status") in ACTIVE:
            raise HTTPException(409, "任务排队或运行中,请先取消")
        shutil.rmtree(job_dir)
        logger.info(f"[{job_id}] 任务已删除")
        return {"ok": True, "job_id": job_id}

    @app.get("/api/jobs/{job_id}/file")
    def job_file(job_id: str, path: str = Query(...)) -> FileResponse:
        job_dir = _job_dir(job_id).resolve()
        target = (job_dir / path).resolve()
        if not target.is_relative_to(job_dir) or not target.is_file():
            raise HTTPException(404, "文件不存在或路径非法")
        return FileResponse(target)

    # ------------------------------------------------------------ 工作流

    @app.get("/api/steps")
    def list_steps() -> list[dict[str, Any]]:
        return wf.step_catalog()

    @app.get("/api/workflows")
    def list_workflows() -> list[dict[str, Any]]:
        return wf.list_workflows(settings)

    @app.get("/api/workflows/{workflow_id}")
    def get_workflow(workflow_id: str) -> dict[str, Any]:
        try:
            spec = wf.get_workflow(settings, workflow_id)
        except KeyError as e:
            raise HTTPException(404, f"工作流不存在: {workflow_id}") from e
        return {**spec.model_dump(), "builtin": workflow_id in wf.builtin_ids()}

    @app.post("/api/workflows/validate")
    def validate_workflow(spec: wf.WorkflowSpec) -> dict[str, Any]:
        errors = wf.validate_workflow(spec)
        return {"ok": not errors, "errors": errors}

    @app.put("/api/workflows/{workflow_id}")
    def save_workflow(workflow_id: str, spec: wf.WorkflowSpec) -> dict[str, Any]:
        if spec.id != workflow_id:
            raise HTTPException(400, "路径中的 id 与工作流 id 不一致")
        try:
            wf.save_workflow(settings, spec)
        except wf.WorkflowError as e:
            raise HTTPException(400, ";".join(e.errors)) from e
        logger.info(f"工作流已保存: {workflow_id}")
        return {"ok": True, "id": workflow_id}

    @app.delete("/api/workflows/{workflow_id}")
    def delete_workflow(workflow_id: str) -> dict[str, Any]:
        try:
            wf.delete_workflow(settings, workflow_id)
        except wf.WorkflowError as e:
            raise HTTPException(400, ";".join(e.errors)) from e
        except KeyError as e:
            raise HTTPException(404, f"工作流不存在: {workflow_id}") from e
        return {"ok": True}

    # ------------------------------------------------------------ 抖音登录

    login_state: dict[str, Any] = {"status": "idle", "error": None}
    login_lock = threading.Lock()

    @app.post("/api/login/douyin")
    def login_douyin_api() -> dict[str, Any]:
        with login_lock:
            if login_state["status"] == "running":
                return {"ok": False, "status": "running", "detail": "登录流程进行中"}
            login_state.update(status="running", error=None)
        from vh_core.publishers.douyin import login_douyin

        cookies_path = settings.publish_cookies / "douyin.json"

        def _do_login() -> None:
            try:
                login_douyin(cookies_path)
                login_state.update(status="done")
            except Exception as e:
                logger.exception("抖音登录失败")
                login_state.update(status="failed", error=str(e))

        threading.Thread(target=_do_login, daemon=True, name="vh-douyin-login").start()
        return {"ok": True, "status": "running"}

    @app.get("/api/login/douyin/status")
    def login_douyin_status() -> dict[str, Any]:
        return {**login_state, "cookie_exists": (settings.publish_cookies / "douyin.json").exists()}

    # ------------------------------------------------------------ 工作室

    from .studio_routes import mount_studio

    mount_studio(app, settings, queue)

    # ------------------------------------------------------------ 前端静态托管

    dist = PROJECT_ROOT / "web" / "dist"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        """SPA 托管:静态文件直出,其余路径回退 index.html(history 路由前端消化)。"""
        if full_path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        if not dist.is_dir():
            raise HTTPException(404, "前端未构建:请先 cd web && npm run build")
        target = (dist / full_path).resolve()
        if full_path and target.is_file() and target.is_relative_to(dist.resolve()):
            # vite 产物文件名带内容哈希,可长期缓存
            immutable = full_path.startswith("assets/")
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if immutable else None
            return FileResponse(target, headers=headers)
        # index.html 每次校验,保证发版后用户拿到新的资源引用
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
