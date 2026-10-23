"""封面生成 — Settings 适配层,实现在 vh_core.media。

extract_frame:ffmpeg 抽帧,默认取时长中点;
add_multicolor_text:PIL 多色多行文字(高亮词分色、描边、自动居中),纯
PIL 无 ffmpeg 依赖,直接透传。
"""

from __future__ import annotations

from pathlib import Path

from .. import media as _media
from ..config import Settings
from ..ffmpeg_utils import get_ffmpeg

add_multicolor_text = _media.add_multicolor_text


def extract_frame(settings: Settings, video: Path, output: Path, frame_time: float | None = None) -> Path:
    """抽视频一帧做封面底图;frame_time 缺省取时长中点。"""
    return _media.extract_frame(video, output, frame_time=frame_time, ffmpeg=get_ffmpeg(settings))
