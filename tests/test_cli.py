"""CLI:工作流相关命令(不加载模型)。"""

from __future__ import annotations

import json

import pytest
import typer
from typer.testing import CliRunner

from vh_cli import main as cli

runner = CliRunner()


def test_version() -> None:
    r = runner.invoke(cli.app, ["--version"])
    assert r.exit_code == 0 and "videohuman" in r.stdout


def test_workflows_list_and_show() -> None:
    r = runner.invoke(cli.app, ["workflows", "list"])
    assert r.exit_code == 0
    assert "broll_voiceover" in {w["id"] for w in json.loads(r.stdout)}
    r = runner.invoke(cli.app, ["workflows", "show", "video_dub"])
    assert r.exit_code == 0
    assert any("原视频" in x for x in json.loads(r.stdout)["required"])
    assert runner.invoke(cli.app, ["workflows", "show", "nope"]).exit_code == 1


def test_workflows_validate(tmp_path) -> None:
    f = tmp_path / "w.json"
    f.write_text(json.dumps({"id": "x", "title": "x", "nodes": [{"id": "v", "type": "tts"}]}), encoding="utf-8")
    r = runner.invoke(cli.app, ["workflows", "validate", str(f)])
    assert r.exit_code == 1 and "未连线" in r.stdout


def test_steps() -> None:
    r = runner.invoke(cli.app, ["steps"])
    assert r.exit_code == 0 and "broll" in {s["type"] for s in json.loads(r.stdout)}


def test_merge_sets() -> None:
    out = cli._merge_sets({}, ["script.text=你好", "montage.min_shot=1.5", "post.postprocess.bgm=false"])
    assert out == {"script": {"text": "你好"}, "montage": {"min_shot": 1.5}, "post": {"postprocess.bgm": False}}
    with pytest.raises(typer.Exit):
        cli._merge_sets({}, ["bad"])


def test_run_workflow_passes_node_params(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_run_job(spec, resume=True):
        seen["spec"] = spec
        from vh_core.models import JobManifest

        return JobManifest(job_id=spec.job_id)

    monkeypatch.setattr(cli, "run_job", fake_run_job)
    r = runner.invoke(cli.app, ["run", "--job-id", "c1", "-w", "broll_voiceover", "-s", "script.text=hi"])
    assert r.exit_code == 0, r.stdout
    assert seen["spec"].params == {"workflow": "broll_voiceover", "node_params": {"script": {"text": "hi"}}}
