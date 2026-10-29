"""工作流:校验规则、连线执行、内置模板端到端(真实 ffmpeg + 假 TTS/ASR/LLM,不需要 GPU)。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import ClassVar

import pytest

from vh_core import workflow as wf
from vh_core.api import run_job
from vh_core.config import PROJECT_ROOT, Settings
from vh_core.ffmpeg_utils import probe_duration
from vh_core.models import JobSpec, StageStatus
from vh_core.pipeline import PipelineStageError, expand_dotted
from vh_core.stages.broll import plan_shots, split_sentences

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="需要 ffmpeg")


def _spec(nodes, edges, **kw) -> wf.WorkflowSpec:
    def e(src, dst):
        s, sh = src.split(".")
        t, th = dst.split(".")
        return {"source": s, "sourceHandle": sh, "target": t, "targetHandle": th}

    return wf.WorkflowSpec.model_validate(
        {
            "id": "t",
            "title": "t",
            "nodes": [{"id": i, "type": t} for i, t in nodes],
            "edges": [e(*x) for x in edges],
            **kw,
        }
    )


# ---------------------------------------------------------------- 校验


def test_builtin_templates_are_valid() -> None:
    ids = wf.builtin_ids()
    assert {"digital_human", "broll_voiceover", "video_translate", "video_dub"} <= ids
    for i in ids:
        spec = wf.get_workflow(Settings(), i)
        assert wf.validate_workflow(spec) == [], i
        assert wf.final_ref(spec) == "post.final"


def test_validation_catches_common_mistakes() -> None:
    errs = wf.validate_workflow(_spec([("a", "nope")], []))
    assert any("未知" in x for x in errs)

    # 类型不匹配:文本连到视频输入
    errs = wf.validate_workflow(_spec([("t", "input_text"), ("p", "postprocess")], [("t.text", "p.video")]))
    assert any("类型不匹配" in x for x in errs)

    # 必需输入未连线
    errs = wf.validate_workflow(_spec([("v", "tts")], []))
    assert any("必需输入" in x for x in errs)

    # 同一输入两条线 + 端口不存在
    errs = wf.validate_workflow(
        _spec(
            [("a", "input_text"), ("b", "input_text"), ("r", "rewrite")],
            [("a.text", "r.text"), ("b.text", "r.text"), ("a.nope", "r.text")],
        )
    )
    assert any("只能连一条线" in x for x in errs)
    assert any("没有输出" in x for x in errs)

    # 环路
    errs = wf.validate_workflow(
        _spec([("x", "rewrite"), ("y", "rewrite")], [("x.text", "y.text"), ("y.text", "x.text")])
    )
    assert any("环路" in x for x in errs)

    # 非法节点 id(用作目录名)
    errs = wf.validate_workflow(_spec([("../x", "input_text")], []))
    assert any("节点 id 非法" in x for x in errs)


def test_video_output_can_feed_audio_input() -> None:
    spec = _spec([("v", "input_video"), ("asr", "transcribe")], [("v.video", "asr.audio")])
    assert wf.validate_workflow(spec) == []


def test_required_params_and_dotted_expansion() -> None:
    spec = wf.get_workflow(Settings(), "broll_voiceover")
    assert any("口播文案" in x for x in wf.missing_required_params(spec, {}))
    assert wf.missing_required_params(spec, {"script": {"text": "你好"}}) == []
    assert expand_dotted({"postprocess.bgm": False, "postprocess.subtitle": True, "x": 1}) == {
        "postprocess": {"bgm": False, "subtitle": True},
        "x": 1,
    }


def test_save_list_delete_user_workflow(settings: Settings) -> None:
    settings.workflows_dir = settings.workspace.parent / "wfs"
    spec = wf.get_workflow(settings, "broll_voiceover").model_copy(update={"id": "my_flow", "title": "我的"})
    wf.save_workflow(settings, spec)
    listed = {w["id"]: w for w in wf.list_workflows(settings)}
    assert listed["my_flow"]["builtin"] is False and listed["broll_voiceover"]["builtin"] is True
    assert wf.get_workflow(settings, "my_flow").title == "我的"
    with pytest.raises(wf.WorkflowError):
        wf.save_workflow(settings, spec.model_copy(update={"id": "digital_human"}))
    with pytest.raises(wf.WorkflowError):
        wf.delete_workflow(settings, "digital_human")
    wf.delete_workflow(settings, "my_flow")
    with pytest.raises(KeyError):
        wf.get_workflow(settings, "my_flow")


# ---------------------------------------------------------------- 纯函数


def test_split_sentences() -> None:
    assert split_sentences("第一句。第二句!第三句\n第四句") == ["第一句。", "第二句!", "第三句", "第四句"]
    assert split_sentences("Hi there. How are you? Fine") == ["Hi there.", "How are you?", "Fine"]


def test_plan_shots_merges_short_and_splits_long() -> None:
    shots = plan_shots([1.0, 1.5, 9.0], total=12.0, min_shot=2.0, max_shot=5.0)
    assert abs(sum(shots) - 12.0) < 1e-6
    assert all(2.0 - 1e-6 <= d <= 5.0 + 1e-6 for d in shots)
    assert plan_shots([], total=3.0, min_shot=2.0, max_shot=5.0) == [3.0]
    assert plan_shots([], total=1.0, min_shot=2.0, max_shot=5.0) == [1.0]


# ---------------------------------------------------------------- 端到端(ffmpeg)


def _ff(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


class FakeTTS:
    """按文本长度生成正弦波,模拟配音。"""

    calls: ClassVar[list[dict]] = []

    def synthesize(self, text: str, out_path: Path, **kw) -> Path:
        FakeTTS.calls.append({"text": text, **kw})
        _ff("-f", "lavfi", "-i", f"sine=f=300:d={1.5 + len(text) * 0.02:.2f}", "-ar", "24000", str(out_path))
        return out_path


class FakeASR:
    def transcribe(self, audio: Path, **kw) -> dict:
        return {"text": "你好世界。今天天气不错。", "sentences": [], "words": [], "timestamp": []}


@pytest.fixture
def fake_providers(monkeypatch: pytest.MonkeyPatch):
    import vh_core.providers as providers
    import vh_core.stages.transcribe as transcribe_mod
    import vh_core.stages.tts as tts_mod

    def get_provider(kind, settings, engine=None):
        return {"tts": FakeTTS(), "asr": FakeASR()}.get(kind) or pytest.fail(f"unexpected provider {kind}")

    FakeTTS.calls = []
    for mod in (providers, tts_mod, transcribe_mod):
        monkeypatch.setattr(mod, "get_provider", get_provider)


@pytest.fixture
def media_settings(settings: Settings) -> Settings:
    font_dir = settings.assets / "font"
    font_dir.mkdir(parents=True)
    shutil.copy(PROJECT_ROOT / "assets" / "font" / "AlibabaPuHuiTi-3-55-Regular.ttf", font_dir)
    broll = settings.assets / "broll"
    broll.mkdir()
    _ff("-f", "lavfi", "-i", "color=c=red:s=640x480:d=1", "-frames:v", "1", str(broll / "a.png"))
    _ff("-f", "lavfi", "-i", "testsrc=s=320x240:d=1.5", "-pix_fmt", "yuv420p", str(broll / "b.mp4"))
    return settings


@needs_ffmpeg
def test_broll_voiceover_end_to_end(media_settings: Settings, fake_providers) -> None:
    s = media_settings
    text = "第一句话讲背景。第二句话讲问题!第三句话给出答案。最后一句总结一下。"
    node_params = {
        "script": {"text": text},
        "montage": {"media_dir": str(s.assets / "broll"), "width": 360, "height": 640, "fps": 15, "min_shot": 0.8},
    }
    manifest = run_job(
        JobSpec(job_id="br1", params={"workflow": "broll_voiceover", "node_params": node_params}), settings=s
    )
    st = manifest.stages
    assert [st[n].status for n in ("script", "polish", "voice", "montage", "post")] == [StageStatus.SUCCESS] * 5
    assert st["publish"].status == StageStatus.SKIPPED  # 未填平台
    final = s.workspace / "br1" / st["post"].outputs["final"]
    voice = s.workspace / "br1" / st["voice"].outputs["speech"]
    # 画面轨与配音等长(不能提前结束画面)
    video_dur = float(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=duration",
                "-of",
                "csv=p=0",
                str(final),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert abs(video_dur - probe_duration(s, voice)) < 0.1
    assert FakeTTS.calls[0]["text"] == text  # 无 LLM key:润色直通
    assert (s.workspace / "br1" / "workflow.json").is_file()  # 快照
    # 续跑:全部已完成,不再调用 TTS
    run_job(JobSpec(job_id="br1", params={"workflow": "broll_voiceover", "node_params": node_params}), settings=s)
    assert len(FakeTTS.calls) == 1


@needs_ffmpeg
def test_video_dub_end_to_end(media_settings: Settings, fake_providers, monkeypatch: pytest.MonkeyPatch) -> None:
    s = media_settings
    s.llm_api_key = "sk-test"
    seen: dict = {}

    class _Completions:
        def create(self, **kw):
            seen.update(kw)
            msg = types.SimpleNamespace(content="Hello world.\nThe weather is nice today.")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    class OpenAI:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(completions=_Completions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=OpenAI))
    src = s.assets / "talk.mp4"
    _ff("-f", "lavfi", "-i", "testsrc=s=360x640:d=4", "-f", "lavfi", "-i", "sine=f=500:d=4",
        "-shortest", "-pix_fmt", "yuv420p", str(src))  # fmt: skip

    manifest = run_job(
        JobSpec(job_id="dub1", params={"workflow": "video_dub", "node_params": {"source": {"path": str(src)}}}),
        settings=s,
    )
    st = manifest.stages
    assert all(r.status == StageStatus.SUCCESS for r in st.values()), {k: (v.status, v.error) for k, v in st.items()}
    assert "英语" in seen["messages"][0]["content"]
    job = s.workspace / "dub1"
    assert (job / st["trans"].outputs["text"]).read_text(encoding="utf-8").startswith("Hello world.")
    # TTS 用截取的原声作参考音色,配音被对齐到原视频时长
    assert FakeTTS.calls[0]["ref_audio"] == job / st["ref"].outputs["audio"]
    assert abs(probe_duration(s, job / st["fit"].outputs["audio"]) - 4.0) < 0.15
    sub = (job / st["post"].outputs["subtitle"]).read_text(encoding="utf-8")
    assert "Hello world" in sub
    meta = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format_tags=AIGC",
                "-of",
                "json",
                str(job / st["post"].outputs["final"]),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert json.loads(meta["format"]["tags"]["AIGC"])["ProduceID"] == "dub1"


@needs_ffmpeg
def test_missing_input_param_fails_cleanly(media_settings: Settings, fake_providers) -> None:
    with pytest.raises(PipelineStageError) as ei:
        run_job(JobSpec(job_id="bad", params={"workflow": "broll_voiceover"}), settings=media_settings)
    assert ei.value.stage_name == "script"


def test_unknown_workflow(settings: Settings) -> None:
    with pytest.raises(wf.WorkflowError):
        run_job(JobSpec(job_id="x", params={"workflow": "no_such_flow"}), settings=settings)
