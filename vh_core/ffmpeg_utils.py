"""ffmpeg 解析与执行 — Settings 适配层,实现在 vh_core.media。

解析链保持原样:config ffmpeg_bin → PATH → imageio-ffmpeg 内置
(后两步委托 vh_core.media.get_ffmpeg,其支持 env VH_FFMPEG 覆盖)。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import media as _media
from .config import Settings


def get_ffmpeg(settings: Settings) -> str:
    """按解析链返回可用的 ffmpeg 可执行文件路径,找不到则报错。"""
    cand = settings.ffmpeg_bin
    if shutil.which(cand):
        return shutil.which(cand)  # type: ignore[return-value]
    return _media.get_ffmpeg()


def run_ffmpeg(settings: Settings, args: list[str], desc: str = "", cwd=None) -> None:
    """执行 ffmpeg(-y 覆盖输出、-nostdin 防交互挂起),失败抛 RuntimeError 并带 stderr 尾部。"""
    _media.run_ffmpeg(args, desc=desc, cwd=cwd, ffmpeg=get_ffmpeg(settings))


def probe_duration(settings: Settings, media: Path) -> float:
    """音频/视频时长(秒),读不到则报错。"""
    import re
    import subprocess

    r = subprocess.run(
        [get_ffmpeg(settings), "-hide_banner", "-i", str(media)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr or "")
    if not m:
        raise RuntimeError(f"无法读取媒体时长: {media}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
