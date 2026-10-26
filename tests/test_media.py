"""画面和声音的纯逻辑,外加一次真正调用本机 ffmpeg 的小冒烟。

不下载模型。ffmpeg 用来生成一段不到一秒的测试视频,确认「找得到程序、读得懂时长和分辨率」。
静音检测用程序自己造的 wav,不依赖麦克风。
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from vh_core.config import PROJECT_ROOT, load_settings
from vh_core.ffmpeg_utils import get_ffmpeg as get_ffmpeg_from_settings
from vh_core.media import (
    _detect_silence_numpy,
    _frames_to_segments,
    _hex_to_rgb,
    _split_highlights,
    add_ai_watermark,
    add_multicolor_text,
    detect_silences,
    extract_frame,
    get_ffmpeg,
    pick_font_file,
    probe_video,
)


def test_hex_color_and_highlight_prefers_longer_word() -> None:
    """颜色写法有三种;高亮词按长的优先,避免「数字」把「数字人」拆开。"""
    assert _hex_to_rgb("#FFD600") == (255, 214, 0)
    assert _hex_to_rgb("rgb(1, 2, 3)") == (1, 2, 3)
    assert _hex_to_rgb("rgba(4, 5, 6, 0.5)") == (4, 5, 6)
    assert _hex_to_rgb("不是颜色") == (0, 0, 0)
    assert _hex_to_rgb("#fff") == (0, 0, 0)

    pieces = _split_highlights("学会数字人口播", ["数字", "数字人"], "#FFFFFF", "#FFD600")
    assert pieces == [("学会", "#FFFFFF"), ("数字人", "#FFD600"), ("口播", "#FFFFFF")]


def test_silence_frames_become_segments() -> None:
    """静音检测的核心是把一串「这一帧静不静」收成时间段。短于门槛的静音留着。"""
    silent = [False, True, True, True, False, True, False]
    # 每一帧 10 毫秒。索引 1 到 3 是 30 毫秒静音;索引 5 只有 10 毫秒,应该被丢掉。
    times = [i * 0.01 for i in range(len(silent))]
    segments = _frames_to_segments(silent, times, hop_sec=0.01, min_dur=0.02)
    assert len(segments) == 1
    assert segments[0]["start"] == pytest.approx(0.01)
    assert segments[0]["end"] == pytest.approx(0.04)


def test_numpy_silence_detector_finds_a_long_gap(tmp_path: Path) -> None:
    """造一段「说话—长静音—说话」。超过 3 秒的静音要被找出来,短静音不要。"""
    wav = tmp_path / "gap.wav"
    _write_gap_wav(wav)
    segments = _detect_silence_numpy(wav, noise_db=-30, min_dur=3)
    assert len(segments) == 1
    assert segments[0]["start"] < 1
    assert segments[0]["end"] > 4

    # 对外的 detect_silences 会把结果缓存成 JSON,第二次读缓存,不再重算。
    cached = detect_silences(wav, tmp_path / "work", min_dur=3, noise_db=-30)
    assert len(cached) == 1
    assert list((tmp_path / "work").glob("*.json"))


def test_pick_font_skips_garbage_and_finds_a_real_font(tmp_path: Path) -> None:
    """字体目录里夹着坏文件时跳过它;仓库自带的字体要能写出「字幕」这两个字。"""
    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"this is not a font")
    assert pick_font_file(tmp_path, preferred="bad.ttf") is None

    real = pick_font_file(PROJECT_ROOT / "assets" / "font", sample_text="字幕")
    assert real is not None
    assert real.suffix.lower() in {".ttf", ".otf", ".ttc"}


def test_cover_text_is_drawn_and_empty_text_copies_the_image(tmp_path: Path) -> None:
    """封面:两行都是空的就原样复制;有字就画上去。AI 标识用 PIL 默认字体也能画。"""
    font = pick_font_file(PROJECT_ROOT / "assets" / "font", sample_text="封面高亮")
    assert font is not None
    src = tmp_path / "base.png"
    Image.new("RGB", (640, 360), "black").save(src)

    copied = tmp_path / "copied.png"
    add_multicolor_text(src, copied, "  ", "", None, font)
    assert copied.read_bytes() == src.read_bytes()

    titled = tmp_path / "titled.png"
    add_multicolor_text(src, titled, "封面标题", "副标题", ["封面"], font, font_size=48)
    assert titled.exists()
    assert _differs(src, titled)

    marked = tmp_path / "marked.png"
    add_ai_watermark(titled, marked, text="此封面为AI生成", font_dir=font.parent, font_size=24)
    assert marked.exists()
    assert _differs(titled, marked)


def test_settings_ffmpeg_and_tiny_video_roundtrip(tmp_path: Path) -> None:
    """冒烟:配置里的 ffmpeg 能找到,读得懂一段自己生成的小视频,也能抽出一帧封面。"""
    exe = get_ffmpeg_from_settings(load_settings())
    assert Path(exe).exists()
    assert Path(get_ffmpeg()).exists()

    video = tmp_path / "tiny.mp4"
    _make_tiny_video(exe, video)
    duration, width, height = probe_video(video, ffmpeg=exe)
    assert duration == pytest.approx(0.4, abs=0.15)
    assert (width, height) == (160, 120)

    frame = tmp_path / "frame.png"
    extract_frame(video, frame, frame_time=0.1, ffmpeg=exe)
    with Image.open(frame) as image:
        assert image.size == (160, 120)


def _write_gap_wav(path: Path) -> None:
    """0.5 秒 440Hz 正弦 + 4 秒全零 + 0.5 秒正弦,16kHz 单声道。"""
    sr = 16000
    tone_n = int(0.5 * sr)
    gap_n = int(4 * sr)
    t = np.arange(tone_n) / sr
    tone = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    gap = np.zeros(gap_n, dtype=np.int16)
    samples = np.concatenate([tone, gap, tone])
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())


def _make_tiny_video(ffmpeg: str, dest: Path) -> None:
    """用 ffmpeg 自带的测试画面生成 0.4 秒、160x120 的 mp4。失败就跳过,不假装通过。"""
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=160x120:d=0.4",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=0.4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        pytest.skip(f"这台机器的 ffmpeg 生成不了测试视频: {exc}")


def _differs(left: Path, right: Path) -> bool:
    with Image.open(left) as a, Image.open(right) as b:
        return a.tobytes() != b.tobytes()
