"""水印 — Settings 适配层,实现在 vh_core.media。

add_text_watermark:ffmpeg drawtext 视频文字水印(textfile= 方案绕转义,
fontfile 相对 work_dir 绕 Windows 盘符问题);
add_ai_watermark / pick_font_file:纯 PIL/fontTools,直接透传。
"""

from __future__ import annotations

from pathlib import Path

from .. import media as _media
from ..config import Settings
from ..ffmpeg_utils import get_ffmpeg

FONT_EXTS = _media.FONT_EXTS

pick_font_file = _media.pick_font_file
add_ai_watermark = _media.add_ai_watermark


def add_text_watermark(
    settings: Settings,
    video: Path,
    output: Path,
    work_dir: Path,
    text: str,
    font_dir: Path,
    font_size: int = 60,
    font_color: str = "white",
    position: str = "top_right",
    margin: int = 40,
) -> Path:
    """给视频右上角(默认)加文字水印,音频直拷。video/output 须位于 work_dir。"""
    return _media.add_text_watermark(
        video,
        output,
        work_dir,
        text,
        font_dir,
        font_size=font_size,
        font_color=font_color,
        position=position,
        margin=margin,
        ffmpeg=get_ffmpeg(settings),
    )
