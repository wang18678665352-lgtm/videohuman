"""冒烟测试:项目能不能被 import,命令行能不能起来,Web 接口的门锁是否锁好。

冒烟的意思是「先点着,看会不会立刻冒烟」。
这里不跑数字人模型,也不访问抖音或大模型接口。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from vh_cli.main import app
from vh_core.capability import CapabilityInfo, GpuInfo, detect_capability, ensure_cuda
from vh_core.config import load_settings
from vh_core.providers import get_provider
from vh_core.stages.download import DouyinDownloadError, _storage_state_from_cookie, get_modal_id


def test_core_packages_import() -> None:
    """这三块是项目自己的代码。import 失败通常是缺依赖,或者文件里有语法错误。"""
    import vh_cli.main
    import vh_core
    import vh_core.api
    import vh_server.main

    assert vh_core.__doc__
    assert vh_cli.main.app is app
    assert vh_server.main.app.title == "videohuman 工作台"


def test_cli_help_and_capability_json() -> None:
    """`vh --help` 能列出子命令;`vh capability` 即使没有显卡也要退出并给出 JSON。"""
    runner = CliRunner()
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "capability" in help_result.stdout
    assert "run" in help_result.stdout

    # 没给 spec 也没给 job-id,应该拒绝,而不是开始跑流水线。
    missing = runner.invoke(app, ["run"])
    assert missing.exit_code == 2

    capability = runner.invoke(app, ["capability"])
    assert capability.exit_code == 0
    data = json.loads(capability.stdout)
    assert "has_cuda" in data
    assert "gpus" in data
    if data["has_cuda"]:
        assert data["gpus"]
        assert data["error"] is None
    else:
        assert data["error"]
        assert "CUDA" in data["error"]


def test_ensure_cuda_explains_the_problem_without_calling_a_driver() -> None:
    """把「没有显卡」这件事做成一条明确的错误,而不是等到模型加载时才崩。"""
    with pytest.raises(RuntimeError, match="CUDA"):
        ensure_cuda(CapabilityInfo(has_cuda=False))
    # 有显卡时不应抛错。这里传入假数据,不读取本机。
    ensure_cuda(CapabilityInfo(has_cuda=True, gpus=[GpuInfo(name="fake", vram_gb=12)], source="test"))


def test_nvidia_smi_output_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """显卡探测优先读 nvidia-smi 的文本。这里喂一行假输出,不要求机器真有 NVIDIA 驱动。"""

    class _Result:
        stdout = "NVIDIA GeForce RTX 4090, 24576\nbad line without number\n"

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _Result())
    info = detect_capability()
    assert info.source == "nvidia-smi"
    assert info.has_cuda is True
    assert info.gpus[0].name == "NVIDIA GeForce RTX 4090"
    assert info.gpus[0].vram_gb == round(24576 / 1024, 1)
    assert info.max_vram_gb == info.gpus[0].vram_gb


def test_detect_capability_does_not_crash_on_this_machine() -> None:
    """不打桩时也必须能返回结果。这台机器可能没有显卡,source 就应该是 none 或 torch。"""
    info = detect_capability()
    assert info.source in {"nvidia-smi", "torch", "none"}
    assert isinstance(info.has_cuda, bool)


def test_unknown_engine_is_rejected_before_loading_models() -> None:
    """引擎名字写错时立刻说明可选值。不能为了报错去 import 那一堆模型代码。"""
    settings = load_settings()
    with pytest.raises(ValueError, match="未知 TTS"):
        get_provider("tts", settings, engine="no-such-tts")
    with pytest.raises(ValueError, match="未知数字人"):
        get_provider("avatar", settings, engine="no-such-avatar")
    with pytest.raises(ValueError, match="未知 provider"):
        get_provider("not-a-kind", settings)  # type: ignore[arg-type]


def test_douyin_id_is_parsed_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """视频号可以直接从链接里读出来。短链才需要联网,联网失败要变成看得懂的错误。"""
    assert get_modal_id("https://www.douyin.com/video/1234567890123") == "1234567890123"
    assert get_modal_id("打开看看 https://www.douyin.com/discover?modal_id=42 复制此链接") == "42"
    with pytest.raises(DouyinDownloadError, match="无法从输入中识别"):
        get_modal_id("这不是链接")

    def _offline(*args, **kwargs):
        import requests

        raise requests.RequestException("offline")

    monkeypatch.setattr("vh_core.stages.download.requests.get", _offline)
    with pytest.raises(DouyinDownloadError, match="短链跳转失败"):
        get_modal_id("https://v.douyin.com/abc/")

    class _Response:
        url = "https://www.douyin.com/video/555"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("vh_core.stages.download.requests.get", lambda *args, **kwargs: _Response())
    assert get_modal_id("分享 https://v.douyin.com/xyz/ 的视频") == "555"


def test_cookie_string_becomes_playwright_storage() -> None:
    """老的 cookie 字符串要能拆成一条条 name=value;没有等号的片段丢掉。"""
    state = _storage_state_from_cookie("sessionid=abc; broken; uid=1")
    assert [item["name"] for item in state["cookies"]] == ["sessionid", "uid"]
    assert state["cookies"][0]["value"] == "abc"
    assert state["cookies"][0]["domain"] == ".douyin.com"
    assert _storage_state_from_cookie("")["cookies"] == []


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把 Web 服务的目录指到临时文件夹,避免测试读到你自己的 workspace。"""
    import vh_server.main as srv

    assets = tmp_path / "assets"
    assets.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(srv, "settings", load_settings(assets_dir=assets, workspace_dir=workspace))
    monkeypatch.setattr(srv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(srv, "_WEB_TOKEN", "")
    monkeypatch.setattr(srv, "_dist", tmp_path / "dist")
    with TestClient(srv.app) as client:
        yield client, srv, tmp_path


def test_settings_api_and_optional_token(api) -> None:
    """工作台设置接口能打开;设了 VH_WEB_TOKEN 之后,不带令牌就进不去。"""
    client, srv, _tmp = api
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["default_tts_engine"] == "cosyvoice3"
    assert "rewrite" in body["stage_names"]
    assert body["llm_configured"] is False

    srv._WEB_TOKEN = "secret"
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers={"X-VH-Token": "secret"}).status_code == 200


def test_job_and_asset_paths_cannot_escape(api) -> None:
    """文件接口只许读任务自己的文件。相邻任务、以及名字只是前缀相同的目录,都不能读。"""
    client, srv, tmp = api
    workspace = tmp / "workspace"
    job = workspace / "demo"
    job.mkdir()
    (job / "note.txt").write_text("hello", encoding="utf-8")
    (job / "manifest.json").write_text('{"job_id": "demo", "stages": {}}', encoding="utf-8")
    secret_job = workspace / "demo-secret"
    secret_job.mkdir()
    (secret_job / "secret.txt").write_text("nope", encoding="utf-8")

    detail = client.get("/api/jobs/demo")
    assert detail.status_code == 200
    assert detail.json()["job_id"] == "demo"
    assert client.get("/api/jobs/missing").status_code == 404

    # `..` 不是合法任务名。直接调用函数,避免 HTTP 客户端把路径里的 .. 自己折叠掉。
    with pytest.raises(HTTPException) as caught:
        srv.job_detail("..")
    assert caught.value.status_code == 404

    own = client.get("/api/jobs/demo/file", params={"path": "note.txt"})
    assert own.status_code == 200
    assert own.text == "hello"
    escaped = client.get("/api/jobs/demo/file", params={"path": "../demo-secret/secret.txt"})
    assert escaped.status_code == 404

    assets = tmp / "assets"
    (assets / "ok.txt").write_text("ok", encoding="utf-8")
    sibling = tmp / "assets2"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("secret", encoding="utf-8")
    assert client.get("/api/asset-file", params={"path": "assets/ok.txt"}).text == "ok"
    # assets2 的开头正好是 assets。旧的 startswith 判断会把这个文件放出去。
    assert client.get("/api/asset-file", params={"path": "assets2/secret.txt"}).status_code == 404


def test_create_job_rejects_bad_input_without_queueing_work(api) -> None:
    """参数不合法时返回 400,并且不能把任务塞进队列(队列里的任务会去跑真正的流水线)。"""
    client, srv, _tmp = api
    both = client.post("/api/jobs", json={"text": "文案", "share_url": "https://v.douyin.com/abc/"})
    assert both.status_code == 400
    empty = client.post("/api/jobs", json={})
    assert empty.status_code == 400
    bad_id = client.post("/api/jobs", json={"job_id": "../x", "text": "文案"})
    assert bad_id.status_code == 400
    assert srv._job_q.empty()


def test_spa_does_not_serve_files_outside_dist(api) -> None:
    """前端没构建时要提示去 npm run build;构建了也不能通过 ../ 读到 dist 外面的文件。"""
    client, srv, tmp = api
    missing = client.get("/")
    assert missing.status_code == 404
    assert "npm run build" in missing.json()["detail"]

    dist = tmp / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
    leak_dir = tmp / "dist-secret"
    leak_dir.mkdir()
    (leak_dir / "leak.txt").write_text("leak", encoding="utf-8")
    srv._dist = dist

    assert client.get("/app.js").text == "console.log(1)"
    assert "app" in client.get("/no-such-page").text
    response = srv.spa("../dist-secret/leak.txt")
    assert Path(response.path).name == "index.html"
