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

from vh_core import __version__
from vh_core import capability as cap_mod
from vh_core.api import load_batch, load_spec, run_batch, run_job
from vh_core.config import load_settings
from vh_core.models import STAGE_NAMES, JobSpec
from vh_core.pipeline import PipelineStageError
from vh_core.workflow import WorkflowError

app = typer.Typer(help="videohuman — 数字人口播视频流水线(纯本地)", no_args_is_help=True)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _setup_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"videohuman {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="显示版本号并退出"
    ),
) -> None:
    """videohuman — 数字人口播视频流水线(纯本地)。"""


def _parse_value(raw: str) -> Any:
    """--set 的值:能按 JSON 解析的(数字/布尔/列表)按 JSON,否则当字符串。"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _merge_sets(node_params: dict[str, dict[str, Any]], sets: list[str]) -> dict[str, dict[str, Any]]:
    for item in sets:
        key, sep, raw = item.partition("=")
        node, dot, param = key.partition(".")
        if not sep or not dot or not node or not param:
            typer.echo(f"错误:--set 格式应为 节点.参数=值,收到: {item}", err=True)
            raise typer.Exit(code=2)
        node_params.setdefault(node, {})[param] = _parse_value(raw)
    return node_params


def _build_spec(job_id: str, params: dict[str, Any]) -> JobSpec:
    try:
        return JobSpec(job_id=job_id, params=params)
    except ValueError as e:
        typer.echo(f"错误:{e}", err=True)
        raise typer.Exit(code=2) from e


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
    _emit(
        {
            "has_cuda": info.has_cuda,
            "error": error,
            "detect_source": info.source,
            "gpus": [{"name": g.name, "vram_gb": g.vram_gb} for g in info.gpus],
        }
    )


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
    workflow: str | None = typer.Option(
        None, "--workflow", "-w", help="按工作流执行:模板 id 或 JSON 文件(见 vh workflows list)"
    ),
    set_: list[str] = typer.Option(
        [], "--set", "-s", help="工作流节点参数,可重复:节点.参数=值,如 script.text=你好 或 montage.order=random"
    ),
    no_resume: bool = typer.Option(False, "--no-resume", help="忽略 manifest,全部重跑"),
    json_out: bool = typer.Option(False, "--json", help="输出结构化 JSON(manifest)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """整跑:经典三模式(audio 直接对口型 / share_url 克隆 / text 直给),或 --workflow 按工作流执行。支持断点续跑。"""
    _setup_logging(verbose)
    if spec is None and not job_id:
        typer.echo("错误:--spec 或 --job-id 必须给一个", err=True)
        raise typer.Exit(code=2)
    spec_obj = load_spec(spec) if spec else None
    jid = job_id or spec_obj.job_id
    params = dict(spec_obj.params) if spec_obj else {}
    for k, v in {
        "audio": audio,
        "text": text,
        "share_url": share_url,
        "douyin_cookie": douyin_cookie,
        "ref_audio": ref_audio,
        "ref_text": ref_text,
        "avatar_video": avatar_video,
        "publish_title": publish_title,
        "tts_engine": tts_engine,
    }.items():
        if v is not None:
            params[k] = v
    if publish:
        params["publish_platforms"] = [p.strip() for p in publish.split(",") if p.strip()]
    if publish_tags:
        params["publish_tags"] = [t.strip() for t in publish_tags.split(",") if t.strip()]
    if workflow:
        params["workflow"] = workflow
    if set_:
        params["node_params"] = _merge_sets(params.get("node_params") or {}, set_)
    try:
        manifest = run_job(_build_spec(jid, params), resume=not no_resume)
    except WorkflowError as e:
        _emit({"ok": False, "error": str(e), "errors": e.errors})
        raise typer.Exit(code=2) from e
    except PipelineStageError as e:
        _emit({"ok": False, "failed_stage": e.stage_name, "error": str(e)})
        raise typer.Exit(code=1) from e
    _emit({"ok": True, "manifest": json.loads(manifest.model_dump_json())})


workflows_app = typer.Typer(help="工作流:列出/查看/校验(在工作台里可视化编辑)", no_args_is_help=True)
app.add_typer(workflows_app, name="workflows")


@workflows_app.command("list")
def workflows_list() -> None:
    """列出内置模板与自定义工作流。"""
    from vh_core import workflow as wf

    _emit(wf.list_workflows(load_settings()))


@workflows_app.command("show")
def workflows_show(workflow_id: str = typer.Argument(..., help="工作流 id")) -> None:
    """输出工作流 JSON,以及新建任务时必须填写的参数。"""
    from vh_core import workflow as wf

    try:
        spec = wf.get_workflow(load_settings(), workflow_id)
    except KeyError as e:
        typer.echo(f"工作流不存在: {workflow_id}", err=True)
        raise typer.Exit(code=1) from e
    _emit({"workflow": spec.model_dump(exclude_none=True), "required": wf.missing_required_params(spec, {})})


@workflows_app.command("validate")
def workflows_validate(file: Path = typer.Argument(..., help="工作流 JSON 文件")) -> None:
    """校验工作流文件(节点类型、连线类型、必需输入、环路)。"""
    from vh_core import workflow as wf

    spec = wf.WorkflowSpec.model_validate(json.loads(file.read_text(encoding="utf-8")))
    errors = wf.validate_workflow(spec)
    _emit({"ok": not errors, "errors": errors})
    if errors:
        raise typer.Exit(code=1)


@app.command()
def steps() -> None:
    """列出工作流可用的全部步骤(端口与参数)。"""
    from vh_core.workflow import step_catalog

    _emit(step_catalog())


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
    allow_no_auth: bool = typer.Option(
        False, "--allow-no-auth", help="允许在非本机地址上无 token 启动(不安全,仅限可信内网调试)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """启动 Web 工作台(FastAPI + web/dist 静态托管)。"""
    _setup_logging(verbose)
    import uvicorn

    settings = load_settings()
    if not settings.web_token.strip() and host not in LOOPBACK_HOSTS:
        if not allow_no_auth:
            typer.echo(
                f"错误:监听 {host} 会把工作台暴露给其他机器,但未设置访问令牌 VH_WEB_TOKEN。\n"
                '  生成令牌: python -c "import secrets;print(secrets.token_urlsafe(32))"\n'
                "  确认要在可信内网无鉴权运行,加 --allow-no-auth",
                err=True,
            )
            raise typer.Exit(code=2)
        logger.warning(f"监听 {host} 且未启用鉴权,任何能访问该端口的人都能操作工作台")

    # 服务端日志按天滚动落盘,保留 14 天,便于事后排障
    settings.logs.mkdir(parents=True, exist_ok=True)
    logger.add(
        settings.logs / "server_{time:YYYY-MM-DD}.log",
        level="DEBUG" if verbose else "INFO",
        rotation="00:00",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
    )
    typer.echo(f"videohuman {__version__} 工作台: http://{host}:{port}")
    uvicorn.run("vh_server.main:app", host=host, port=port, log_level="info")


@app.command()
def doctor(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """环境自检:GPU / ffmpeg / 模型权重 / 字体 / 前端构建 / 鉴权配置,输出 JSON。"""
    _setup_logging(verbose)
    from vh_core.config import PROJECT_ROOT
    from vh_core.ffmpeg_utils import get_ffmpeg

    s = load_settings()
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "", required: bool = True) -> None:
        checks.append({"name": name, "ok": ok, "required": required, "detail": detail})

    info = cap_mod.detect_capability()
    check("cuda_gpu", info.has_cuda, ", ".join(f"{g.name} {g.vram_gb}GB" for g in info.gpus) or "未检测到 GPU")
    try:
        check("ffmpeg", True, get_ffmpeg(s))
    except RuntimeError as e:
        check("ffmpeg", False, str(e))
    check("asr_model", (s.models / "SenseVoiceSmall").is_dir(), str(s.models / "SenseVoiceSmall"))
    tts_dir = s.cosyvoice_model if s.tts_engine == "cosyvoice3" else s.indextts_model
    check(f"tts_model({s.tts_engine})", tts_dir.is_dir(), str(tts_dir))
    if s.avatar_engine == "lstmsync":
        lstm = s.abs_path(Path(s.lstmsync_dir))
        check("avatar_engine(lstmsync)", lstm.is_dir(), str(lstm))
    else:
        mt = s.musetalk_repo / "models"
        check("avatar_engine(musetalk)", mt.is_dir(), str(mt))
    fonts = s.assets / "font"
    check("subtitle_fonts", fonts.is_dir() and any(fonts.glob("*.tt[fc]")), str(fonts), required=False)
    check("llm_api_key", bool(s.llm_api_key), "未配置时 rewrite 直通原文", required=False)
    check(
        "web_dist",
        (PROJECT_ROOT / "web" / "dist" / "index.html").exists(),
        "cd web && npm ci && npm run build",
        required=False,
    )
    check("web_token", bool(s.web_token.strip()), "对外提供服务时必须设置 VH_WEB_TOKEN", required=False)
    ok = all(c["ok"] for c in checks if c["required"])
    _emit({"ok": ok, "version": __version__, "checks": checks})
    if not ok:
        raise typer.Exit(code=1)


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
        for k, v in {
            "audio": audio,
            "text": text,
            "share_url": share_url,
            "douyin_cookie": douyin_cookie,
            "ref_audio": ref_audio,
            "ref_text": ref_text,
            "avatar_video": avatar_video,
            "publish_title": publish_title,
        }.items():
            if v is not None:
                params[k] = v
        if publish:
            params["publish_platforms"] = [p.strip() for p in publish.split(",") if p.strip()]
        if publish_tags:
            params["publish_tags"] = [t.strip() for t in publish_tags.split(",") if t.strip()]
        try:
            manifest = run_job(_build_spec(job_id, params), only=[stage_name], resume=not no_resume)
        except PipelineStageError as e:
            _emit({"ok": False, "failed_stage": e.stage_name, "error": str(e)})
            raise typer.Exit(code=1) from e
        _emit({"ok": True, "manifest": json.loads(manifest.model_dump_json())})

    return cmd


for _name in STAGE_NAMES:
    app.command(name=_name)(_single_stage(_name))


if __name__ == "__main__":
    app()
