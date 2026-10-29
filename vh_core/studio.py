"""单机工作室商用查询:机器是否就绪、产量、授权日志、成片交付包。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from vh_core.capability import detect_capability
from vh_core.config import PROJECT_ROOT, Settings
from vh_core.ffmpeg_utils import get_ffmpeg
from vh_core.models import validate_job_id

_ACTIVE = frozenset({"queued", "running", "cancelling"})
_JOB_MARKERS = ("manifest.json", "status.server.json", "spec.server.json")
_FONT_GLOBS = ("*.ttf", "*.ttc", "*.otf")
_MODEL_ACTION = "运行 uv run python download_weights.py all"
_NO_FINAL = "尚无成片，无法出交付包"

DELIVERY_NOTE = """\
本压缩包是成片交付物。
画面带有 AI 生成标识。
MP4 含 AIGC 元数据字段。
运营须留存授权同意记录。
发布到抖音时，可能需要人工通过验证码。
"""


def readiness(settings: Settings) -> dict[str, Any]:
    """环境是否能出片。ok 只看必查项;CUDA 仅展示,不单独决定 ok。"""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, *, required: bool, action: str) -> None:
        checks.append({"name": name, "ok": ok, "required": required, "detail": detail, "action": action})

    info = detect_capability()
    gpu_detail = ", ".join(f"{g.name} {g.vram_gb}GB" for g in info.gpus) or "未检测到 GPU"
    add(
        "cuda_gpu",
        info.has_cuda,
        gpu_detail,
        required=False,
        action="安装 NVIDIA 驱动并确认 nvidia-smi 可用",
    )
    try:
        ffmpeg_detail = get_ffmpeg(settings)
        ffmpeg_ok = True
    except RuntimeError as exc:
        ffmpeg_detail = str(exc)
        ffmpeg_ok = False
    add("ffmpeg", ffmpeg_ok, ffmpeg_detail, required=True, action="安装 ffmpeg 并确保在 PATH")

    asr_dir = settings.models / "SenseVoiceSmall"
    add("asr_model", asr_dir.is_dir(), str(asr_dir), required=True, action=_MODEL_ACTION)

    tts_dir = _tts_model_dir(settings)
    add(
        f"tts_model({settings.tts_engine})",
        tts_dir.is_dir(),
        str(tts_dir),
        required=True,
        action=_MODEL_ACTION,
    )

    avatar_name, avatar_dir, avatar_action = _avatar_check(settings)
    add(avatar_name, avatar_dir.is_dir(), str(avatar_dir), required=True, action=avatar_action)

    fonts = settings.assets / "font"
    add(
        "subtitle_fonts",
        fonts.is_dir() and any(any(fonts.glob(pattern)) for pattern in _FONT_GLOBS),
        str(fonts),
        required=False,
        action="将 .ttf/.ttc/.otf 字体放入 assets/font",
    )
    add(
        "llm_api_key",
        bool(settings.llm_api_key),
        "未配置时 rewrite 直通原文",
        required=False,
        action="需要润色时设置环境变量 VH_LLM_API_KEY",
    )
    web_dist = PROJECT_ROOT / "web" / "dist" / "index.html"
    add(
        "web_dist",
        web_dist.is_file(),
        "cd web && npm ci && npm run build",
        required=False,
        action="cd web && npm ci && npm run build",
    )
    add(
        "web_token",
        bool(settings.web_token.strip()),
        "对外提供服务时必须设置 VH_WEB_TOKEN",
        required=False,
        action="对外提供服务时设置环境变量 VH_WEB_TOKEN",
    )
    ok = all(item["ok"] for item in checks if item["required"])
    return {"ok": ok, "commercial_mode": bool(settings.commercial_mode), "checks": checks}


def usage_summary(settings: Settings) -> dict[str, Any]:
    """扫描 workspace 下的任务目录,汇总产量。损坏的 JSON 跳过,不抛错。"""
    summary = {
        "jobs_total": 0,
        "done": 0,
        "failed": 0,
        "active": 0,
        "interrupted": 0,
        "with_final": 0,
        "commercial_mode": bool(settings.commercial_mode),
    }
    workspace = settings.workspace
    if not workspace.is_dir():
        return summary
    for child in workspace.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not any((child / name).is_file() for name in _JOB_MARKERS):
            continue
        status, with_final = _job_usage(child)
        summary["jobs_total"] += 1
        if status in _ACTIVE:
            summary["active"] += 1
        elif status in ("done", "failed", "interrupted"):
            summary[status] += 1
        if with_final:
            summary["with_final"] += 1
    return summary


def iter_consent_lines(settings: Settings) -> list[dict[str, Any]]:
    """读取 assets/.consent.jsonl。文件不存在返回空列表,坏行跳过。"""
    path = settings.assets / ".consent.jsonl"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def build_delivery_zip(settings: Settings, job_id: str) -> bytes:
    """把成片、清单、文案和交付说明打成内存 zip。无成片或路径越界则抛 FileNotFoundError。"""
    validate_job_id(job_id)
    job_dir = settings.workspace / job_id
    if not job_dir.is_dir():
        raise FileNotFoundError(f"任务不存在: {job_id}")

    manifest = _read_json_object(job_dir, "manifest.json") or {}
    outputs = _post_outputs(manifest)
    final_path = _contained_file(job_dir, outputs.get("final"))
    if final_path is None:
        raise FileNotFoundError(_NO_FINAL)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("DELIVERY.txt", DELIVERY_NOTE)
        _write_if_inside(zf, job_dir, "manifest.json")
        _write_if_inside(zf, job_dir, "spec.server.json")
        _write_path(zf, job_dir, final_path)
        cover = outputs.get("cover")
        if isinstance(cover, str):
            _write_if_inside(zf, job_dir, cover)
        _write_if_inside(zf, job_dir, "rewrite/text.txt")
    return buf.getvalue()


def _tts_model_dir(settings: Settings) -> Path:
    if settings.tts_engine == "cosyvoice3":
        found = getattr(settings, "cosyvoice_model", None)
        name = Path(settings.cosyvoice_model_dir).name
    else:
        found = getattr(settings, "indextts_model", None)
        name = Path(settings.indextts_model_dir).name
    if isinstance(found, Path):
        return found
    return settings.models / name


def _avatar_check(settings: Settings) -> tuple[str, Path, str]:
    if settings.avatar_engine == "lstmsync":
        return (
            "avatar_engine(lstmsync)",
            settings.abs_path(Path(settings.lstmsync_dir)),
            "将 LstmSync 引擎放到 lstmsync_dir(默认 third_party/lstmsync)",
        )
    repo = getattr(settings, "musetalk_repo", None)
    models = repo / "models" if isinstance(repo, Path) else PROJECT_ROOT / "third_party" / "MuseTalk" / "models"
    return ("avatar_engine(musetalk)", models, _MODEL_ACTION)


def _job_usage(job_dir: Path) -> tuple[str, bool]:
    status_doc = _read_json_object(job_dir, "status.server.json")
    manifest = _read_json_object(job_dir, "manifest.json") or {}
    queue_status = status_doc.get("queue_status") if status_doc else None
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    if queue_status in _ACTIVE:
        status = str(queue_status)
    elif any(isinstance(stage, dict) and stage.get("status") == "failed" for stage in stages.values()):
        status = "failed"
    else:
        post = stages.get("postprocess")
        post_status = post.get("status") if isinstance(post, dict) else None
        if post_status in ("success", "skipped"):
            status = "done"
        elif stages:
            status = "interrupted"
        else:
            status = "unknown"
    final = _post_outputs(manifest).get("final")
    return status, isinstance(final, str) and bool(final.strip())


def _post_outputs(manifest: dict[str, Any]) -> dict[str, Any]:
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        return {}
    post = stages.get("postprocess")
    if not isinstance(post, dict):
        return {}
    outputs = post.get("outputs")
    return outputs if isinstance(outputs, dict) else {}


def _read_json_object(job_dir: Path, rel: str) -> dict[str, Any] | None:
    path = _contained_file(job_dir, rel)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _contained_file(job_dir: Path, rel: object) -> Path | None:
    """解析 rel,要求落在 job 目录内且是文件。越界、损坏路径或目录都返回 None。"""
    if not isinstance(rel, str) or not rel.strip():
        return None
    root = job_dir.resolve()
    try:
        target = (root / rel).resolve()
        target.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    if not target.is_file():
        return None
    return target


def _write_if_inside(zf: zipfile.ZipFile, job_dir: Path, rel: str) -> None:
    path = _contained_file(job_dir, rel)
    if path is not None:
        _write_path(zf, job_dir, path)


def _write_path(zf: zipfile.ZipFile, job_dir: Path, path: Path) -> None:
    arc = path.resolve().relative_to(job_dir.resolve()).as_posix()
    zf.write(path, arcname=arc)
