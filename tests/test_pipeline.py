"""流水线骨架的测试:数据模型、断点续跑、任务说明书(spec)。

这些测试不碰显卡,也不联网。环节(stage)用几行假的类代替,
只用来观察「编排器有没有按规矩调用它们」。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vh_core.api import load_batch, load_spec, run_job
from vh_core.config import PROJECT_ROOT, load_settings
from vh_core.models import JobManifest, JobSpec, StageResult, StageStatus
from vh_core.pipeline import Pipeline, PipelineStageError, Stage, StageContext


def _settings(tmp_path: Path):
    """测试用的配置:产物写到临时目录,不污染仓库里的 workspace/。"""
    return load_settings(workspace_dir=tmp_path)


class _OkStage(Stage):
    """假装成功的环节:在自己的目录里写一个 out.txt。"""

    name = "ok"

    def run(self, ctx: StageContext) -> dict[str, Path]:
        out = ctx.stage_dir / "out.txt"
        out.write_text(ctx.params.get("text", "ok"), encoding="utf-8")
        return {"out": out}


class _BoomStage(Stage):
    """假装失败的环节:一运行就抛错。"""

    name = "boom"

    def run(self, ctx: StageContext) -> dict[str, Path]:
        raise RuntimeError("爆炸了")


class _MaybeStage(Stage):
    """只有参数 go 为真才执行,用来覆盖「本任务用不上这个环节」。"""

    name = "maybe"

    def enabled(self, ctx: StageContext) -> bool:
        return bool(ctx.params.get("go"))

    def run(self, ctx: StageContext) -> dict[str, Path]:
        out = ctx.stage_dir / "out.txt"
        out.write_text("yes", encoding="utf-8")
        return {"out": out}


def test_stage_result_duration_and_manifest_done() -> None:
    """耗时只有「开始和结束都有」才算得出来;没跑过的环节不算完成。"""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    done = StageResult(
        name="rewrite",
        status=StageStatus.SUCCESS,
        started_at=start,
        finished_at=start + timedelta(seconds=2),
    )
    assert done.duration_s == 2
    assert StageResult(name="x", status=StageStatus.PENDING).duration_s is None

    manifest = JobManifest(job_id="job")
    assert manifest.is_done("rewrite") is False
    manifest.stages["rewrite"] = done
    assert manifest.is_done("rewrite") is True
    manifest.stages["rewrite"].status = StageStatus.FAILED
    assert manifest.is_done("rewrite") is False


def test_stage_context_records_posix_relative_path(tmp_path: Path) -> None:
    """产物路径要记成相对任务目录的形式,而且用正斜杠,Windows 上也不会写成反斜杠。"""
    job_dir = tmp_path / "job"
    stage_dir = job_dir / "rewrite"
    stage_dir.mkdir(parents=True)
    text = stage_dir / "text.txt"
    text.write_text("你好", encoding="utf-8")
    ctx = StageContext(
        settings=_settings(tmp_path),
        job_id="job",
        params={},
        stage_dir=stage_dir,
        job_dir=job_dir,
    )
    assert ctx.rel(text) == "rewrite/text.txt"


def test_pipeline_success_writes_manifest_and_output(tmp_path: Path) -> None:
    """正常跑完:磁盘上有 manifest.json,状态是 success,产物路径是相对路径。"""
    pipe = Pipeline([_OkStage()], _settings(tmp_path))
    manifest = pipe.run("job-1", params={"text": "你好"})

    assert manifest.stages["ok"].status == StageStatus.SUCCESS
    assert manifest.stages["ok"].outputs["out"] == "ok/out.txt"
    assert (tmp_path / "job-1" / "ok" / "out.txt").read_text(encoding="utf-8") == "你好"

    saved = json.loads((tmp_path / "job-1" / "manifest.json").read_text(encoding="utf-8"))
    assert saved["stages"]["ok"]["status"] == "success"


def test_pipeline_failure_is_recorded_then_raised(tmp_path: Path) -> None:
    """环节抛错时:先把 failed 写进 manifest,再把错误抛出去,调用方能知道是哪一步。"""
    pipe = Pipeline([_BoomStage()], _settings(tmp_path))
    with pytest.raises(PipelineStageError) as caught:
        pipe.run("job-1")

    assert caught.value.stage_name == "boom"
    assert "爆炸了" in str(caught.value.cause)
    saved = json.loads((tmp_path / "job-1" / "manifest.json").read_text(encoding="utf-8"))
    assert saved["stages"]["boom"]["status"] == "failed"
    assert "爆炸了" in saved["stages"]["boom"]["error"]


def test_failed_stage_does_not_wipe_earlier_success(tmp_path: Path) -> None:
    """后面的环节失败时,前面已经成功的环节必须仍是 success。

    以前的写法会在「跳过已完成环节」时立刻把状态改成 skipped。
    后面一步失败落盘,会把这份改动一起写进去,下次续跑又把第一步重做。
    """
    state = {"runs": 0, "fail": True}

    class First(Stage):
        name = "first"

        def run(self, ctx: StageContext) -> dict[str, Path]:
            state["runs"] += 1
            out = ctx.stage_dir / "out.txt"
            out.write_text("first", encoding="utf-8")
            return {"out": out}

    class Second(Stage):
        name = "second"

        def run(self, ctx: StageContext) -> dict[str, Path]:
            if state["fail"]:
                raise RuntimeError("失败一次")
            out = ctx.stage_dir / "out.txt"
            out.write_text("second", encoding="utf-8")
            return {"out": out}

    pipe = Pipeline([First(), Second()], _settings(tmp_path))
    with pytest.raises(PipelineStageError):
        pipe.run("job-1")

    saved = json.loads((tmp_path / "job-1" / "manifest.json").read_text(encoding="utf-8"))
    assert saved["stages"]["first"]["status"] == "success"
    assert saved["stages"]["second"]["status"] == "failed"
    assert state["runs"] == 1

    # 第二次:第二步修好了。第一步已经成功,不应该再跑。
    state["fail"] = False
    manifest = pipe.run("job-1")
    assert state["runs"] == 1
    assert manifest.stages["first"].status == StageStatus.SKIPPED
    assert manifest.stages["second"].status == StageStatus.SUCCESS
    # 返回值里标了 skipped,只表示「这一次没重跑」。磁盘上仍是 success,下次还认得。
    disk = json.loads((tmp_path / "job-1" / "manifest.json").read_text(encoding="utf-8"))
    assert disk["stages"]["first"]["status"] == "success"


def test_disabled_stage_is_skipped_and_only_limits_which_stages_run(tmp_path: Path) -> None:
    """不适用的环节记成 skipped;only 指定谁跑,其他环节连记录都不写。"""
    settings = _settings(tmp_path)
    skipped = Pipeline([_MaybeStage()], settings).run("job-skip", params={})
    assert skipped.stages["maybe"].status == StageStatus.SKIPPED
    assert not (tmp_path / "job-skip" / "maybe" / "out.txt").exists()

    # 打开开关就会真的跑。
    ran = Pipeline([_MaybeStage()], settings).run("job-go", params={"go": True})
    assert ran.stages["maybe"].status == StageStatus.SUCCESS

    only = Pipeline([_OkStage(), _BoomStage()], settings).run("job-only", only=["ok"])
    assert "ok" in only.stages
    assert "boom" not in only.stages


def test_resume_false_reruns_even_if_already_succeeded(tmp_path: Path) -> None:
    """resume=False 表示「别看旧清单,全部重跑」。"""
    state = {"runs": 0}

    class Once(Stage):
        name = "once"

        def run(self, ctx: StageContext) -> dict[str, Path]:
            state["runs"] += 1
            out = ctx.stage_dir / "out.txt"
            out.write_text("x", encoding="utf-8")
            return {"out": out}

    pipe = Pipeline([Once()], _settings(tmp_path))
    pipe.run("job-1")
    pipe.run("job-1", resume=False)
    assert state["runs"] == 2


def test_load_example_specs() -> None:
    """仓库自带的示例说明书能被读进来,批量任务会继承 defaults。"""
    spec = load_spec(PROJECT_ROOT / "examples" / "job.toml")
    assert spec.job_id == "example-001"
    assert "厌学" in spec.params["text"]
    assert spec.params["tts"]["speed"] == 1.0

    batch = load_batch(PROJECT_ROOT / "examples" / "batch.toml")
    assert [item.job_id for item in batch] == ["batch-001", "batch-002", "batch-003"]
    # 每条 job 自己没写 avatar_video,应该从 defaults.params 继承过来。
    assert batch[0].params["avatar_video"] == "assets/avatar.mp4"
    assert batch[0].params["bgm_volume"] == 0.12
    assert "厌学" in batch[0].params["text"]


def test_load_json_spec_and_reject_empty_batch(tmp_path: Path) -> None:
    """spec 也支持 JSON;批量文件一个 job 都没有时要明确报错,而不是悄悄什么都不做。"""
    spec_path = tmp_path / "job.json"
    spec_path.write_text(json.dumps({"job_id": "j", "params": {"text": "你好"}}), encoding="utf-8")
    spec = load_spec(spec_path)
    assert spec == JobSpec(job_id="j", params={"text": "你好"})

    empty = tmp_path / "empty.toml"
    empty.write_text("title = 'nothing'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="没有 jobs"):
        load_batch(empty)


def test_rewrite_passthrough_without_api_key(tmp_path: Path) -> None:
    """没配 LLM 密钥时,rewrite 不联网,把原文写进 text.txt。这是主链路的安全默认行为。"""
    settings = load_settings(workspace_dir=tmp_path, llm_api_key="")
    manifest = run_job(JobSpec(job_id="demo", params={"text": "原文案"}), settings=settings, only=["rewrite"])
    assert manifest.stages["rewrite"].status == StageStatus.SUCCESS
    assert (tmp_path / "demo" / "rewrite" / "text.txt").read_text(encoding="utf-8") == "原文案"


def test_download_stage_skips_when_no_share_url(tmp_path: Path) -> None:
    """没有抖音链接就不下载。这条测试如果去联网,说明跳过条件坏了。"""
    settings = load_settings(workspace_dir=tmp_path)
    manifest = run_job(JobSpec(job_id="demo", params={}), settings=settings, only=["download"])
    assert manifest.stages["download"].status == StageStatus.SKIPPED


def test_settings_env_and_absolute_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量 VH_ 盖过默认值;代码里直接传入的参数再盖过环境变量。"""
    monkeypatch.setenv("VH_LLM_MODEL", "from-env")
    assert load_settings().llm_model == "from-env"
    assert load_settings(llm_model="from-init").llm_model == "from-init"

    settings = load_settings(workspace_dir=tmp_path)
    assert settings.workspace == tmp_path.resolve()
    # 不传时用项目里的相对目录,并解析成绝对路径。
    assert load_settings().workspace == (PROJECT_ROOT / "workspace").resolve()
