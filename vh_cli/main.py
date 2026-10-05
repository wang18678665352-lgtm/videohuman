"""vh CLI 入口(typer)。

- `vh run` 整跑流水线(--spec 文件驱动,或 --job-id + 参数直给,flags 优先于 spec)
- `vh batch` 批量生成(模型跨 job 驻留、失败隔离、汇总 JSON)
- `vh rewrite|tts|avatar|postprocess` 单步跑(输入缺省时取上游 manifest 产物)
- `--json` 输出结构化结果供 agent 解析;业务逻辑全部在 vh_core
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from loguru import logger

from vh_core import capability as cap_mod
from vh_core.api import load_batch, load_spec, run_batch, run_job
from vh_core.config import load_settings
from vh_core.models import JobSpec
from vh_core.pipeline import PipelineStageError

app = typer.Typer(help="videohuman — 数字人口播视频流水线(纯本地)", no_args_is_help=True)

STAGE_NAMES = ["download", "transcribe", "rewrite", "tts", "avatar", "postprocess", "publish"]


def _setup_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")


def _emit(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


@app.command()
def capability(
    json_out: bool = typer.Option(False, "--json", help="输出结构化 JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """检测机器能力(GPU/显存);纯本地版本,无 CUDA 时给出报错提示。"""
    _setup_logging(verbose)
    info = cap_mod.detect_capability()
    try:
        cap_mod.ensure_cuda(info)
        error = None
    except RuntimeError as e:
        error = str(e)
    _emit({
        "has_cuda": info.has_cuda,
        "error": error,
        "detect_source": info.source,
        "gpus": [{"name": g.name, "vram_gb": g.vram_gb} for g in info.gpus],
    })


@app.command()
def run(
    spec: Path | None = typer.Option(None, "--spec", help="JobSpec 文件(TOML/JSON)"),
    job_id: str | None = typer.Option(None, "--job-id", help="任务 ID(无 spec 时必填)"),
    audio: str | None = typer.Option(None, "--audio", help="直接驱动音频路径(跳过 TTS,直接对口型)"),
    text: str | None = typer.Option(None, "--text", help="文案内容(直给模式或配套字幕)"),
    share_url: str | None = typer.Option(None, "--share-url", help="抖音分享链接(克隆模式)"),
    douyin_cookie: str | None = typer.Option(None, "--douyin-cookie", help="抖音 cookie(也可用 VH_DOUYIN_COOKIE)"),
    ref_audio: str | None = typer.Option(None, "--ref-audio", help="TTS 参考音频路径"),
    ref_text: str | None = typer.Option(None, "--ref-text", help="TTS 参考音频对应文本"),
    avatar_video: str | None = typer.Option(None, "--avatar-video", help="数字人素材视频路径"),
    tts_engine: str | None = typer.Option(None, "--tts-engine", help="TTS 引擎: cosyvoice3(本地)"),
    publish: str | None = typer.Option(None, "--publish", help="发布平台,逗号分隔:bilibili,douyin"),
    publish_title: str | None = typer.Option(None, "--publish-title", help="发布标题(缺省取文案首句)"),
    publish_tags: str | None = typer.Option(None, "--publish-tags", help="发布标签,逗号分隔"),
    no_resume: bool = typer.Option(False, "--no-resume", help="忽略 manifest,全部重跑"),
    json_out: bool = typer.Option(False, "--json", help="输出结构化 JSON(manifest)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """整跑流水线(三模式):audio 直接对口型;share_url 走 下载→转写→改写;text 直给。支持断点续跑。"""
    _setup_logging(verbose)
    if spec is None and not job_id:
        typer.echo("错误:--spec 或 --job-id 必须给一个", err=True)
        raise typer.Exit(code=2)
    spec_obj = load_spec(spec) if spec else None
    jid = job_id or spec_obj.job_id
    params = dict(spec_obj.params) if spec_obj else {}
    for k, v in {"audio": audio, "text": text, "share_url": share_url, "douyin_cookie": douyin_cookie,
                 "ref_audio": ref_audio, "ref_text": ref_text,
                 "avatar_video": avatar_video, "publish_title": publish_title,
                 "tts_engine": tts_engine}.items():
        if v is not None:
            params[k] = v
    if publish:
        params["publish_platforms"] = [p.strip() for p in publish.split(",") if p.strip()]
    if publish_tags:
        params["publish_tags"] = [t.strip() for t in publish_tags.split(",") if t.strip()]
    try:
        manifest = run_job(JobSpec(job_id=jid, params=params), resume=not no_resume)
    except PipelineStageError as e:
        _emit({"ok": False, "failed_stage": e.stage_name, "error": str(e)})
        raise typer.Exit(code=1) from e
    _emit({"ok": True, "manifest": json.loads(manifest.model_dump_json())})


@app.command()
def login(
    platform: str = typer.Argument(..., help="平台名: douyin"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """弹出浏览器,登录平台后自动保存登录态(cookie)。"""
    _setup_logging(verbose)
    if platform != "douyin":
        typer.echo(f"暂不支持的平台: {platform}(目前只有 douyin)", err=True)
        raise typer.Exit(code=2)
    from vh_core.publishers.douyin import login_douyin

    settings = load_settings()
    cookies_path = settings.abs_path(settings.publish_cookies_dir) / "douyin.json"
    try:
        login_douyin(cookies_path)
    except Exception as e:
        _emit({"ok": False, "error": str(e)})
        raise typer.Exit(code=1) from e
    _emit({"ok": True, "cookies_path": str(cookies_path)})


@app.command()
def batch(
    file: Path = typer.Option(..., "--file", help="批量文件(TOML/JSON):[defaults.params] + [[jobs]]"),
    no_resume: bool = typer.Option(False, "--no-resume"),
    json_out: bool = typer.Option(False, "--json", help="输出批次报告 JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """批量生成:模型跨 job 驻留,单 job 失败不中断,末输出批次报告。"""
    _setup_logging(verbose)
    specs = load_batch(file)
    report = run_batch(specs, resume=not no_resume)
    _emit(report)
    if not report["ok"]:
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8100, "--port"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """启动 Web 工作台(FastAPI + web/dist 静态托管)。"""
    _setup_logging(verbose)
    import uvicorn

    typer.echo(f"videohuman 工作台: http://{host}:{port}")
    uvicorn.run("vh_server.main:app", host=host, port=port, log_level="info")


def _single_stage(stage_name: str):
    def cmd(
        job_id: str = typer.Option(..., "--job-id"),
        audio: str | None = typer.Option(None, "--audio", help="直接驱动音频路径"),
        text: str | None = typer.Option(None, "--text"),
        share_url: str | None = typer.Option(None, "--share-url"),
        douyin_cookie: str | None = typer.Option(None, "--douyin-cookie"),
        ref_audio: str | None = typer.Option(None, "--ref-audio"),
        ref_text: str | None = typer.Option(None, "--ref-text"),
        avatar_video: str | None = typer.Option(None, "--avatar-video"),
        publish: str | None = typer.Option(None, "--publish", help="发布平台,逗号分隔"),
        publish_title: str | None = typer.Option(None, "--publish-title"),
        publish_tags: str | None = typer.Option(None, "--publish-tags", help="逗号分隔"),
        no_resume: bool = typer.Option(False, "--no-resume"),
        json_out: bool = typer.Option(False, "--json"),
        verbose: bool = typer.Option(False, "--verbose", "-v"),
    ):
        f"""单步运行 {stage_name} stage(输入缺省时取上游 manifest 产物)。"""
        _setup_logging(verbose)
        params: dict[str, Any] = {}
        for k, v in {"audio": audio, "text": text, "share_url": share_url, "douyin_cookie": douyin_cookie,
                     "ref_audio": ref_audio, "ref_text": ref_text,
                     "avatar_video": avatar_video, "publish_title": publish_title}.items():
            if v is not None:
                params[k] = v
        if publish:
            params["publish_platforms"] = [p.strip() for p in publish.split(",") if p.strip()]
        if publish_tags:
            params["publish_tags"] = [t.strip() for t in publish_tags.split(",") if t.strip()]
        try:
            manifest = run_job(JobSpec(job_id=job_id, params=params),
                               only=[stage_name], resume=not no_resume)
        except PipelineStageError as e:
            _emit({"ok": False, "failed_stage": e.stage_name, "error": str(e)})
            raise typer.Exit(code=1) from e
        _emit({"ok": True, "manifest": json.loads(manifest.model_dump_json())})

    return cmd


for _name in STAGE_NAMES:
    app.command(name=_name)(_single_stage(_name))


if __name__ == "__main__":
    app()
