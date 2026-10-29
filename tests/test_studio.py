"""工作室商用接口:就绪、产量、授权日志、交付包。不需要 GPU。"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vh_core.capability import CapabilityInfo
from vh_core.config import Settings
from vh_core.studio import build_delivery_zip, iter_consent_lines, readiness, usage_summary
from vh_server.main import create_app


def test_readiness_missing_models(settings: Settings, tmp_path: Path) -> None:
    settings.models_dir = tmp_path / "models"
    settings.third_party_dir = tmp_path / "third_party"
    settings.cosyvoice_model_dir = str(tmp_path / "models" / "Fun-CosyVoice3-0.5B-2512")
    settings.tts_engine = "cosyvoice3"
    settings.avatar_engine = "musetalk"
    report = readiness(settings)
    assert set(report) == {"ok", "commercial_mode", "checks"}
    assert report["ok"] is False
    assert isinstance(report["commercial_mode"], bool)
    names = [item["name"] for item in report["checks"]]
    assert names == [
        "cuda_gpu",
        "ffmpeg",
        "asr_model",
        "tts_model(cosyvoice3)",
        "avatar_engine(musetalk)",
        "subtitle_fonts",
        "llm_api_key",
        "web_dist",
        "web_token",
    ]
    by_name = {item["name"]: item for item in report["checks"]}
    for item in report["checks"]:
        assert set(item) == {"name", "ok", "required", "detail", "action"}
        assert item["action"]
    ffmpeg = by_name["ffmpeg"]
    assert ffmpeg["required"] is True
    if shutil.which("ffmpeg"):
        assert ffmpeg["ok"] is True
    assert ffmpeg["action"] == "安装 ffmpeg 并确保在 PATH"
    assert by_name["asr_model"]["ok"] is False
    assert by_name["asr_model"]["required"] is True
    assert by_name["asr_model"]["action"] == "运行 uv run python download_weights.py all"
    assert by_name["tts_model(cosyvoice3)"]["ok"] is False
    assert by_name["avatar_engine(musetalk)"]["ok"] is False
    assert by_name["cuda_gpu"]["required"] is False
    assert by_name["subtitle_fonts"]["required"] is False


def test_readiness_ok_without_gpu(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    models = tmp_path / "models"
    (models / "SenseVoiceSmall").mkdir(parents=True)
    cosy = tmp_path / "cosy"
    cosy.mkdir()
    (tmp_path / "third_party" / "MuseTalk" / "models").mkdir(parents=True)
    fonts = settings.assets / "font"
    fonts.mkdir()
    (fonts / "note.otf").write_bytes(b"otf")
    settings.models_dir = models
    settings.cosyvoice_model_dir = str(cosy)
    settings.third_party_dir = tmp_path / "third_party"
    settings.tts_engine = "cosyvoice3"
    settings.avatar_engine = "musetalk"
    monkeypatch.setattr("vh_core.studio.detect_capability", lambda: CapabilityInfo(has_cuda=False))
    report = readiness(settings)
    by_name = {item["name"]: item for item in report["checks"]}
    assert by_name["cuda_gpu"]["ok"] is False
    assert by_name["cuda_gpu"]["required"] is False
    assert by_name["asr_model"]["ok"] is True
    assert by_name["tts_model(cosyvoice3)"]["ok"] is True
    assert by_name["avatar_engine(musetalk)"]["ok"] is True
    assert by_name["subtitle_fonts"]["ok"] is True
    assert report["ok"] is by_name["ffmpeg"]["ok"]


def test_readiness_lstmsync_and_indextts(settings: Settings, tmp_path: Path) -> None:
    settings.avatar_engine = "lstmsync"
    settings.lstmsync_dir = str(tmp_path / "missing-lstm")
    settings.tts_engine = "indextts2"
    settings.indextts_model_dir = str(tmp_path / "missing-index")
    by_name = {item["name"]: item for item in readiness(settings)["checks"]}
    assert by_name["avatar_engine(lstmsync)"]["ok"] is False
    assert by_name["avatar_engine(lstmsync)"]["required"] is True
    assert by_name["tts_model(indextts2)"]["ok"] is False


def test_usage_summary_counts(settings: Settings) -> None:
    def put(
        name: str,
        *,
        manifest: dict | None = None,
        status: dict | None = None,
        spec: dict | None = None,
        raw_manifest: str | None = None,
    ) -> None:
        job = settings.workspace / name
        job.mkdir()
        if raw_manifest is not None:
            (job / "manifest.json").write_text(raw_manifest, encoding="utf-8")
        elif manifest is not None:
            (job / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        if status is not None:
            (job / "status.server.json").write_text(json.dumps(status), encoding="utf-8")
        if spec is not None:
            (job / "spec.server.json").write_text(json.dumps(spec), encoding="utf-8")

    final = {"status": "success", "outputs": {"final": "final.mp4"}}
    put("done1", manifest={"stages": {"postprocess": final}})
    put("skip1", manifest={"stages": {"postprocess": {"status": "skipped", "outputs": {}}}})
    put(
        "running1",
        manifest={"stages": {"postprocess": final}},
        status={"queue_status": "running"},
    )
    put("fail1", manifest={"stages": {"transcribe": {"status": "failed"}}})
    put("part1", manifest={"stages": {"transcribe": {"status": "success"}}})
    put("badjson", raw_manifest="{")
    put("spec1", spec={"text": "你好"})
    put(
        "cancelled-done",
        manifest={"stages": {"postprocess": final}},
        status={"queue_status": "cancelled"},
    )
    hidden = settings.workspace / ".hidden"
    hidden.mkdir()
    (hidden / "manifest.json").write_text("{}", encoding="utf-8")
    (settings.workspace / "not-a-job").mkdir()

    summary = usage_summary(settings)
    assert set(summary) == {
        "jobs_total",
        "done",
        "failed",
        "active",
        "interrupted",
        "with_final",
        "commercial_mode",
    }
    assert summary["jobs_total"] == 8
    assert summary["done"] == 3
    assert summary["failed"] == 1
    assert summary["active"] == 1
    assert summary["interrupted"] == 1
    assert summary["with_final"] == 3
    assert summary["commercial_mode"] is True


def test_build_delivery_zip(settings: Settings) -> None:
    job = settings.workspace / "jobok"
    (job / "rewrite").mkdir(parents=True)
    (job / "final.mp4").write_bytes(b"mp4-bytes")
    (job / "cover.jpg").write_bytes(b"jpg")
    (job / "rewrite" / "text.txt").write_text("文案", encoding="utf-8")
    (job / "spec.server.json").write_text("{}", encoding="utf-8")
    outside = settings.workspace / "secret.jpg"
    outside.write_bytes(b"secret")
    manifest = {
        "stages": {
            "postprocess": {
                "status": "success",
                "outputs": {"final": "final.mp4", "cover": "cover.jpg"},
            }
        }
    }
    (job / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    blob = build_delivery_zip(settings, "jobok")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = set(zf.namelist())
        assert "DELIVERY.txt" in names
        assert zf.read("final.mp4") == b"mp4-bytes"
        note = zf.read("DELIVERY.txt").decode("utf-8")
        assert "交付" in note
        assert "AI" in note
        assert "AIGC" in note
        assert "授权" in note
        assert "验证码" in note
        assert "manifest.json" in names
        assert "spec.server.json" in names
        assert "cover.jpg" in names
        assert zf.read("rewrite/text.txt").decode("utf-8") == "文案"

    bare = settings.workspace / "nofinal"
    bare.mkdir()
    (bare / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="尚无成片"):
        build_delivery_zip(settings, "nofinal")
    with pytest.raises(FileNotFoundError):
        build_delivery_zip(settings, "missing-job")
    with pytest.raises(ValueError):
        build_delivery_zip(settings, "../x")

    evil = settings.workspace / "evil"
    evil.mkdir()
    (evil / "inside.mp4").write_bytes(b"ok")
    (evil / "manifest.json").write_text(
        json.dumps(
            {
                "stages": {
                    "postprocess": {
                        "outputs": {"final": "inside.mp4", "cover": "../secret.jpg"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    packed = build_delivery_zip(settings, "evil")
    with zipfile.ZipFile(io.BytesIO(packed)) as zf:
        assert zf.read("inside.mp4") == b"ok"
        assert "secret.jpg" not in zf.namelist()
        assert all(not name.startswith("..") for name in zf.namelist())

    escaped = settings.workspace / "escaped"
    escaped.mkdir()
    (escaped / "manifest.json").write_text(
        json.dumps({"stages": {"postprocess": {"outputs": {"final": "../secret.mp4"}}}}),
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match="尚无成片"):
        build_delivery_zip(settings, "escaped")


def test_iter_consent_lines_skips_bad_line(settings: Settings) -> None:
    assert iter_consent_lines(settings) == []
    log = settings.assets / ".consent.jsonl"
    log.write_text('{"file": "a.wav"}\nnot-json\n', encoding="utf-8")
    assert iter_consent_lines(settings) == [{"file": "a.wav"}]


def test_studio_routes(settings: Settings) -> None:
    job = settings.workspace / "jobzip"
    job.mkdir()
    (job / "final.mp4").write_bytes(b"vid")
    (job / "manifest.json").write_text(
        json.dumps({"stages": {"postprocess": {"status": "success", "outputs": {"final": "final.mp4"}}}}),
        encoding="utf-8",
    )
    app = create_app(settings, runner=lambda *a, **k: None)
    with TestClient(app) as client:
        usage = client.get("/api/usage")
        assert usage.status_code == 200
        assert set(usage.json()) == {
            "jobs_total",
            "done",
            "failed",
            "active",
            "interrupted",
            "with_final",
            "commercial_mode",
        }
        assert usage.json()["done"] == 1
        assert usage.json()["with_final"] == 1
        ready = client.get("/api/readiness")
        assert ready.status_code == 200
        body = ready.json()
        assert set(body) == {"ok", "commercial_mode", "checks"}
        assert set(body["checks"][0]) == {"name", "ok", "required", "detail", "action"}
        delivered = client.get("/api/jobs/jobzip/delivery")
        assert delivered.status_code == 200
        assert delivered.headers["content-type"].startswith("application/zip")
        assert "jobzip-delivery.zip" in delivered.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(delivered.content)) as zf:
            assert "DELIVERY.txt" in zf.namelist()
            assert zf.read("final.mp4") == b"vid"
        assert client.get("/api/jobs/-bad/delivery").status_code == 400
        missing = client.get("/api/jobs/missingjob/delivery")
        assert missing.status_code == 404
        assert "尚无成片" not in missing.json()["detail"]
        consent = client.get("/api/consent")
        assert consent.status_code == 200
        assert consent.headers["content-type"].startswith("application/x-ndjson")
        assert "consent.jsonl" in consent.headers["content-disposition"]
        assert consent.content == b""
        (settings.assets / ".consent.jsonl").write_text('{"file":"a.wav"}\n', encoding="utf-8")
        exported = client.get("/api/consent")
        assert exported.status_code == 200
        assert b"a.wav" in exported.content
