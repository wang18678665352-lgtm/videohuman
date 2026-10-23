"""静音检测与切除 — Settings 适配层,实现在 vh_core.media。

保持原公开签名(Settings 第一参);内部 get_ffmpeg(settings) 后委托
vh_core.media。检测:librosa 多特征算法优先,回退 numpy RMS;
切除:保留段 trim + 静音区内硬切拼接,临时文件全部落 work_dir。
"""

from __future__ import annotations

from pathlib import Path

from .. import media as _media
from ..config import Settings
from ..ffmpeg_utils import get_ffmpeg

# 切除参数(原值,毫秒级常量)
MAX_SILENCE_LENGTH = _media.MAX_SILENCE_LENGTH
START_SILENCE_KEEP = _media.START_SILENCE_KEEP
END_SILENCE_KEEP = _media.END_SILENCE_KEEP
FADE_LENGTH = _media.FADE_LENGTH

VIDEO_EXTS = _media.VIDEO_EXTS


def probe_video(settings: Settings, video: Path) -> tuple[float, int, int]:
    """返回 (时长秒, 宽, 高)。"""
    return _media.probe_video(video, ffmpeg=get_ffmpeg(settings))


def detect_silences(
    settings: Settings, src: Path, work_dir: Path, min_dur: float = MAX_SILENCE_LENGTH, noise_db: float = -30.0
) -> list[dict]:
    """检测音视频中的静音段,结果缓存到 work_dir(JSON)。"""
    return _media.detect_silences(src, work_dir, min_dur=min_dur, noise_db=noise_db, ffmpeg=get_ffmpeg(settings))


def cut_silence(
    settings: Settings,
    video: Path,
    output: Path,
    work_dir: Path,
    noise_db: float = -30.0,
    min_dur: float = MAX_SILENCE_LENGTH,
) -> Path:
    """切除静音段并交叉淡化拼接。video/output 均须位于 work_dir 内。"""
    return _media.cut_silence(video, output, work_dir, noise_db=noise_db, min_dur=min_dur, ffmpeg=get_ffmpeg(settings))
