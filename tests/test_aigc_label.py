"""AI 生成标识集成测试:真实调用 ffmpeg(无 ffmpeg 时跳过),不需要 GPU。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from vh_core.config import PROJECT_ROOT, Settings
from vh_core.pipeline import StageContext
from vh_core.stages.postprocess import PostprocessStage, aigc_metadata

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="需要 ffmpeg/ffprobe")

# 只开 AI 标识相关步骤,其余后期步骤关掉,测试聚焦且快
PP_MINIMAL = {"silence_cut": False, "subtitle": False, "bgm": False, "cover": False}


def _ctx(settings: Settings, pp: dict) -> StageContext:
    job_dir = settings.workspace / "job1"
    (job_dir / "avatar").mkdir(parents=True)
    (job_dir / "postprocess").mkdir()
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
         "-f", "lavfi", "-i", "sine=f=440:d=1", "-shortest", "-pix_fmt", "yuv420p",
         str(job_dir / "avatar" / "avatar.mp4")],
        check=True,
    )  # fmt: skip
    font_dir = settings.assets / "font"
    font_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(PROJECT_ROOT / "assets" / "font" / "AlibabaPuHuiTi-3-55-Regular.ttf", font_dir)
    return StageContext(
        settings=settings,
        job_id="job1",
        params={"postprocess": pp},
        stage_dir=job_dir / "postprocess",
        job_dir=job_dir,
    )


def _tags(video: Path) -> dict[str, str]:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format_tags", "-of", "json", str(video)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)["format"].get("tags", {})


def _frame_luma(video: Path) -> float:
    """首帧平均亮度:纯黑底片加了白字角标后应明显大于 0。"""
    raw = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return sum(raw) / len(raw)


def test_final_video_has_visible_and_implicit_ai_labels(settings: Settings) -> None:
    ctx = _ctx(settings, dict(PP_MINIMAL))
    out = PostprocessStage().run(ctx)
    final = out["final"]
    tags = _tags(final)
    meta = json.loads(tags["AIGC"])
    assert meta["Label"] == "1"
    assert meta["ContentProducer"] == "videohuman"
    assert meta["ProduceID"] == "job1"
    assert tags["comment"] == settings.aigc_label_text
    assert _frame_luma(final) > 1.0  # 画面角标已烧录
    assert not list(ctx.stage_dir.glob("step*.mp4"))  # 中间文件已清理


def test_disabling_visible_label_keeps_implicit_metadata(settings: Settings) -> None:
    ctx = _ctx(settings, {**PP_MINIMAL, "ai_label": False})
    final = PostprocessStage().run(ctx)["final"]
    assert json.loads(_tags(final)["AIGC"])["Label"] == "1"
    assert _frame_luma(final) < 1.0  # 未加角标,仍是纯黑


def test_visible_label_failure_fails_the_stage(settings: Settings) -> None:
    ctx = _ctx(settings, dict(PP_MINIMAL))
    shutil.rmtree(settings.assets / "font")  # 无字体 → 无法加角标,不得静默产出无标识成片
    with pytest.raises(RuntimeError, match="AI 生成标识"):
        PostprocessStage().run(ctx)


def test_aigc_metadata_format(settings: Settings) -> None:
    meta = json.loads(aigc_metadata(settings, "abc"))
    assert set(meta) == {
        "Label",
        "ContentProducer",
        "ProduceID",
        "ReservedCode1",
        "ContentPropagator",
        "PropagateID",
        "ReservedCode2",
    }
