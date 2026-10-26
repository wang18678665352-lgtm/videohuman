from __future__ import annotations

import json
from pathlib import Path

import pytest

from vh_core.config import Settings
from vh_core.models import StageStatus
from vh_core.pipeline import Pipeline, PipelineCancelled, PipelineStageError, Stage, StageContext, atomic_write_text


class RecordingStage(Stage):
    def __init__(self, name: str, calls: list[str], fail: bool = False, enabled: bool = True):
        self.name = name  # type: ignore[misc]
        self.calls = calls
        self.fail = fail
        self._enabled = enabled

    def enabled(self, ctx: StageContext) -> bool:
        return self._enabled

    def run(self, ctx: StageContext) -> dict[str, Path]:
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} boom")
        out = ctx.stage_dir / "out.txt"
        out.write_text(self.name, encoding="utf-8")
        return {"out": out}


def _pipeline(settings: Settings, calls: list[str], **flags) -> tuple[Pipeline, dict[str, RecordingStage]]:
    stages = {n: RecordingStage(n, calls, **flags.get(n, {})) for n in ("a", "b", "c")}
    return Pipeline(list(stages.values()), settings), stages


def test_runs_all_stages_and_records_outputs(settings: Settings) -> None:
    calls: list[str] = []
    p, _ = _pipeline(settings, calls)
    m = p.run("j1")
    assert calls == ["a", "b", "c"]
    assert all(r.status == StageStatus.SUCCESS for r in m.stages.values())
    assert m.stages["a"].outputs == {"out": "a/out.txt"}
    on_disk = json.loads((settings.workspace / "j1" / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["stages"]["c"]["status"] == "success"


def test_resume_is_stable_across_multiple_runs(settings: Settings) -> None:
    """回归:续跑曾把已完成 stage 改写成 SKIPPED,导致第三次运行重新执行全部 stage。"""
    calls: list[str] = []
    p, _ = _pipeline(settings, calls)
    p.run("j1")
    p.run("j1")
    p.run("j1")
    assert calls == ["a", "b", "c"]
    m = p.load_manifest("j1")
    assert all(r.status == StageStatus.SUCCESS for r in m.stages.values())


def test_failure_marks_stage_and_resume_continues(settings: Settings) -> None:
    calls: list[str] = []
    p, stages = _pipeline(settings, calls, b={"fail": True})
    with pytest.raises(PipelineStageError) as ei:
        p.run("j1")
    assert ei.value.stage_name == "b"
    m = p.load_manifest("j1")
    assert m.stages["a"].status == StageStatus.SUCCESS
    assert m.stages["b"].status == StageStatus.FAILED
    assert "boom" in (m.stages["b"].error or "")
    assert "c" not in m.stages

    stages["b"].fail = False
    p.run("j1")
    assert calls == ["a", "b", "b", "c"]


def test_no_resume_reruns_everything(settings: Settings) -> None:
    calls: list[str] = []
    p, _ = _pipeline(settings, calls)
    p.run("j1")
    p.run("j1", resume=False)
    assert calls == ["a", "b", "c"] * 2


def test_only_runs_selected_stage(settings: Settings) -> None:
    calls: list[str] = []
    p, _ = _pipeline(settings, calls)
    p.run("j1", only=["b"])
    assert calls == ["b"]


def test_only_reruns_completed_stage_and_keeps_others(settings: Settings) -> None:
    """单步重跑:已完成的 stage 也要重新执行;--no-resume 不得清掉其他 stage 的记录。"""
    calls: list[str] = []
    p, _ = _pipeline(settings, calls)
    p.run("j1")
    p.run("j1", only=["b"])
    m = p.run("j1", only=["c"], resume=False)
    assert calls == ["a", "b", "c", "b", "c"]
    assert set(m.stages) == {"a", "b", "c"}
    assert all(r.status == StageStatus.SUCCESS for r in m.stages.values())


def test_disabled_stage_is_skipped(settings: Settings) -> None:
    calls: list[str] = []
    p, _ = _pipeline(settings, calls, a={"enabled": False})
    m = p.run("j1")
    assert calls == ["b", "c"]
    assert m.stages["a"].status == StageStatus.SKIPPED


def test_cancel_stops_at_stage_boundary(settings: Settings) -> None:
    calls: list[str] = []
    p, _ = _pipeline(settings, calls)
    with pytest.raises(PipelineCancelled) as ei:
        p.run("j1", should_cancel=lambda: len(calls) >= 1)
    assert ei.value.stage_name == "b"
    assert calls == ["a"]


def test_rejects_path_traversal_job_id(settings: Settings) -> None:
    p, _ = _pipeline(settings, [])
    with pytest.raises(ValueError):
        p.run("../escape")


def test_atomic_write_text_replaces_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "m.json"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert [p.name for p in tmp_path.iterdir()] == ["m.json"]
