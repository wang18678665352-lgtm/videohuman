"""HTTP API 测试:注入假 runner,不加载模型、不占 GPU。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vh_core.config import Settings
from vh_core.pipeline import PipelineCancelled, PipelineStageError
from vh_server.jobs import STATUS_FILE, JobQueue
from vh_server.main import create_app


class FakeRunner:
    """按 job 记录调用;可配置失败/阻塞,用于验证队列语义。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail: set[str] = set()
        self.gate = threading.Event()
        self.gate.set()

    def __call__(self, job_id, params, only, resume, should_cancel):
        self.calls.append({"job_id": job_id, "params": params, "only": only, "resume": resume})
        while not self.gate.wait(0.01):
            if should_cancel():
                raise PipelineCancelled(job_id, "tts")
        if job_id in self.fail:
            raise PipelineStageError("tts", RuntimeError("显存不足"))
        manifest = {
            "job_id": job_id,
            "created_at": "2026-01-01T00:00:00Z",
            "stages": {"postprocess": {"name": "postprocess", "status": "success", "outputs": {"final": "p/f.mp4"}}},
        }
        d = Path(params["_ws"]) / job_id
        (d / "p").mkdir(parents=True, exist_ok=True)
        (d / "p" / "f.mp4").write_bytes(b"video")
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _wait(pred, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("等待超时")


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def client(settings: Settings, runner: FakeRunner):
    app = create_app(settings, runner=runner)
    with TestClient(app) as c:
        yield c
    runner.gate.set()


def _status(client: TestClient, job_id: str) -> str:
    return client.get(f"/api/jobs/{job_id}").json()["status"]


def _create(client: TestClient, **body) -> Any:
    body.setdefault("text", "你好")
    return client.post("/api/jobs", json=body)


@pytest.fixture(autouse=True)
def _inject_ws(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    orig = JobQueue.submit

    def submit(self, job_id, params, only=None, resume=True):
        # 假 runner 需要知道 workspace 位置,借 params 传入
        return orig(self, job_id, {**params, "_ws": str(settings.workspace)}, only, resume)

    monkeypatch.setattr(JobQueue, "submit", submit)


def test_health_and_settings(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "X-Request-ID" in r.headers
    s = client.get("/api/settings").json()
    assert s["stage_names"][0] == "download" and s["stage_names"][-1] == "publish"


def test_create_job_runs_and_reports_done(client: TestClient, runner: FakeRunner) -> None:
    r = _create(client, job_id="j1")
    assert r.status_code == 200, r.text
    _wait(lambda: _status(client, "j1") == "done")
    detail = client.get("/api/jobs/j1").json()
    assert detail["has_final"] is True
    assert detail["spec"]["text"] == "你好"
    assert [j["job_id"] for j in client.get("/api/jobs").json()] == ["j1"]
    assert client.get("/api/jobs/j1/file", params={"path": "p/f.mp4"}).content == b"video"


def test_create_job_validation(client: TestClient) -> None:
    assert client.post("/api/jobs", json={}).status_code == 400
    assert client.post("/api/jobs", json={"text": "a", "share_url": "https://v.douyin.com/x"}).status_code == 400
    assert client.post("/api/jobs", json={"text": "a", "job_id": "../x"}).status_code == 400
    assert _create(client, job_id="dup").status_code == 200
    assert _create(client, job_id="dup").status_code == 409


@pytest.mark.parametrize("key", ["audio", "ref_audio", "avatar_video"])
def test_create_job_rejects_paths_outside_assets(client: TestClient, key: str) -> None:
    r = client.post("/api/jobs", json={"text": "a", key: "/etc/passwd"})
    assert r.status_code == 400
    r = client.post("/api/jobs", json={"text": "a", key: "assets/../../etc/passwd"})
    assert r.status_code == 400


def test_rerun_params_paths_are_checked(client: TestClient) -> None:
    _create(client, job_id="j")
    _wait(lambda: _status(client, "j") == "done")
    r = client.post("/api/jobs/j/run", json={"params": {"bgm_path": "/etc/passwd"}})
    assert r.status_code == 400
    r = client.post("/api/jobs/j/run", json={"params": {"postprocess": {"bg_image": "../../etc/x.png"}}})
    assert r.status_code == 400


def test_create_job_accepts_asset_paths(client: TestClient, settings: Settings) -> None:
    r = client.post("/api/jobs", json={"text": "a", "avatar_video": str(settings.assets / "a.mp4")})
    assert r.status_code == 200, r.text


def test_failed_job_reports_error_and_can_rerun(client: TestClient, runner: FakeRunner) -> None:
    runner.fail.add("bad")
    _create(client, job_id="bad")
    _wait(lambda: _status(client, "bad") == "failed")
    assert "显存不足" in client.get("/api/jobs/bad").json()["error"]

    runner.fail.clear()
    r = client.post("/api/jobs/bad/run", json={"only": "tts", "resume": True})
    assert r.status_code == 200
    _wait(lambda: _status(client, "bad") == "done")
    assert runner.calls[-1]["only"] == ["tts"]
    assert client.post("/api/jobs/bad/run", json={"only": "nope"}).status_code == 400
    assert client.post("/api/jobs/missing/run", json={}).status_code == 404


def test_rerun_while_running_conflicts_and_cancel(client: TestClient, runner: FakeRunner) -> None:
    runner.gate.clear()
    _create(client, job_id="slow")
    _wait(lambda: _status(client, "slow") == "running")
    assert client.post("/api/jobs/slow/run", json={}).status_code == 409
    assert client.delete("/api/jobs/slow").status_code == 409

    # 排队中的任务可直接取消
    _create(client, job_id="waiting")
    detail = client.get("/api/jobs/waiting").json()
    assert detail["status"] == "queued" and detail["queue_position"] == 1
    assert client.post("/api/jobs/waiting/cancel").json()["status"] == "cancelled"

    # 运行中的任务在 stage 边界取消
    assert client.post("/api/jobs/slow/cancel").json()["status"] == "cancelling"
    _wait(lambda: _status(client, "slow") == "cancelled")
    assert client.post("/api/jobs/slow/cancel").status_code == 409
    assert [c["job_id"] for c in runner.calls] == ["slow"]


def test_delete_job(client: TestClient, settings: Settings) -> None:
    _create(client, job_id="gone")
    _wait(lambda: _status(client, "gone") == "done")
    assert client.delete("/api/jobs/gone").status_code == 200
    assert not (settings.workspace / "gone").exists()
    assert client.get("/api/jobs/gone").status_code == 404


def test_job_file_blocks_traversal(client: TestClient, settings: Settings) -> None:
    (settings.workspace / "secret.txt").write_text("x", encoding="utf-8")
    _create(client, job_id="j")
    _wait(lambda: _status(client, "j") == "done")
    assert client.get("/api/jobs/j/file", params={"path": "../secret.txt"}).status_code == 404
    assert client.get("/api/jobs/j/file", params={"path": "/etc/passwd"}).status_code == 404


def test_asset_upload_list_and_preview(client: TestClient, settings: Settings) -> None:
    r = client.post("/api/assets?kind=audio", files={"file": ("../../evil name.wav", b"RIFF", "audio/wav")})
    assert r.status_code == 200, r.text
    path = r.json()["path"]
    assert Path(path).name == "evil name.wav"
    assert (settings.assets / "evil name.wav").read_bytes() == b"RIFF"
    # 同名再传不覆盖,自动改名
    r2 = client.post("/api/assets?kind=audio", files={"file": ("evil name.wav", b"RIFF2", "audio/wav")})
    assert Path(r2.json()["path"]).name == "evil name_1.wav"
    assert client.post("/api/assets?kind=audio", files={"file": ("x.exe", b"MZ", "x")}).status_code == 400
    assert client.post("/api/assets?kind=audio", files={"file": ("e.wav", b"", "audio/wav")}).status_code == 400
    assert client.get("/api/assets").json()["audios"]
    assert client.get("/api/asset-file", params={"path": path}).content == b"RIFF"
    # 前缀相同的兄弟目录不能绕过素材目录限制
    sibling = settings.assets.parent / (settings.assets.name + "_evil")
    sibling.mkdir()
    (sibling / "x.wav").write_bytes(b"x")
    assert client.get("/api/asset-file", params={"path": str(sibling / "x.wav")}).status_code == 404


def test_upload_size_limit(settings: Settings, runner: FakeRunner) -> None:
    settings.max_upload_mb = 1
    with TestClient(create_app(settings, runner=runner)) as c:
        big = b"0" * (1024 * 1024 + 1)
        r = c.post("/api/assets?kind=video", files={"file": ("big.mp4", big, "video/mp4")})
        assert r.status_code == 413
    assert not list(settings.assets.glob("*big*"))


def test_unknown_api_route_is_404_not_spa(client: TestClient) -> None:
    assert client.get("/api/nope").status_code == 404


def test_token_auth(settings: Settings, runner: FakeRunner) -> None:
    settings.web_token = "s3cret"
    with TestClient(create_app(settings, runner=runner)) as c:
        assert c.get("/api/health").status_code == 200  # 探活免鉴权
        assert c.get("/api/settings").status_code == 401
        assert c.get("/api/settings", headers={"X-VH-Token": "wrong"}).status_code == 401
        assert c.get("/api/settings", headers={"X-VH-Token": "s3cret"}).status_code == 200
        assert c.get("/api/settings", params={"token": "s3cret"}).status_code == 200


def test_token_auth_rate_limit(settings: Settings, runner: FakeRunner) -> None:
    from vh_server import main as server_main

    settings.web_token = "s3cret"
    with TestClient(create_app(settings, runner=runner)) as c:
        for _ in range(server_main.AUTH_FAIL_MAX):
            assert c.get("/api/settings").status_code == 401
        assert c.get("/api/settings", headers={"X-VH-Token": "s3cret"}).status_code == 429


def test_recover_after_restart(settings: Settings, runner: FakeRunner) -> None:
    ws = settings.workspace
    for job_id, qs, ts in (("was-running", "running", 1.0), ("q2", "queued", 3.0), ("q1", "queued", 2.0)):
        (ws / job_id).mkdir()
        (ws / job_id / STATUS_FILE).write_text(json.dumps({"queue_status": qs, "submitted_at": ts}), encoding="utf-8")
        (ws / job_id / "spec.server.json").write_text(json.dumps({"text": job_id, "_ws": str(ws)}), encoding="utf-8")

    with TestClient(create_app(settings, runner=runner)) as c:
        _wait(lambda: _status(c, "q2") == "done")
        assert _status(c, "was-running") == "interrupted"
        assert "重启" in c.get("/api/jobs/was-running").json()["error"]
    # 按提交顺序恢复
    assert [x["job_id"] for x in runner.calls] == ["q1", "q2"]
