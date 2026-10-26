"""平台无关的后期处理:ffmpeg 封装、静音切除、字幕、水印、封面。

- ffmpeg 解析链:env VH_FFMPEG → PATH → imageio-ffmpeg 内置二进制;
- 全部函数路径/参数显式传入,无配置对象、无 assets 目录自动发现;
- process_video 为六步编排:静音切除 → 背景图合成 → 字幕烧录(均分时间轴)
  → BGM 混音 → 水印 → 封面,产物 final.mp4 + cover.png。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import wave
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from loguru import logger

# ------------------------------------------------------------------ ffmpeg

ENV_FFMPEG = "VH_FFMPEG"


@lru_cache(maxsize=1)
def _imageio_ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def get_ffmpeg() -> str:
    """按解析链返回可用的 ffmpeg 可执行文件路径,找不到则报错。"""
    env = os.environ.get(ENV_FFMPEG)
    if env and Path(env).exists():
        return env
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    builtin = _imageio_ffmpeg_exe()
    if builtin:
        logger.debug(f"ffmpeg 走 imageio-ffmpeg 内置: {builtin}")
        return builtin
    raise RuntimeError(f"找不到 ffmpeg(env {ENV_FFMPEG} 未设置/无效,PATH 无 ffmpeg,imageio-ffmpeg 也不可用)")


def run_ffmpeg(args: list[str], desc: str = "", cwd=None, ffmpeg: str | None = None) -> None:
    """执行 ffmpeg(-y 覆盖输出、-nostdin 防交互挂起),失败抛 RuntimeError 并带 stderr 尾部。"""
    exe = ffmpeg or get_ffmpeg()
    cmd = [exe, "-hide_banner", "-y", "-nostdin", *args]
    logger.debug(f"ffmpeg {desc}: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()[-5:]
        raise RuntimeError(f"ffmpeg {desc} 失败(code={r.returncode}): {' | '.join(tail)}")


# ------------------------------------------------------------------ 探测


def probe_video(video: Path, ffmpeg: str | None = None) -> tuple[float, int, int]:
    """返回 (时长秒, 宽, 高)。"""
    exe = ffmpeg or get_ffmpeg()
    r = subprocess.run(
        [exe, "-hide_banner", "-i", str(video)], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    err = r.stderr or ""
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if not m:
        raise RuntimeError(f"无法读取视频时长: {video}")
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    dm = re.search(r"Stream .*?Video:.*? (\d{2,5})x(\d{2,5})", err)
    if not dm:
        raise RuntimeError(f"无法读取视频分辨率: {video}")
    return duration, int(dm.group(1)), int(dm.group(2))


# ------------------------------------------------------------------ 静音检测与切除

# 切除参数(毫秒级常量):静音段内保留 20ms 头尾,切点落在静音内直接硬切。
# 口播视频在静音处姿态/声音都归零,硬切即可;旧版 500ms 交叉淡化会把语音尾巴
# 和下一句开头叠在一起(画面双影 + 声音叠音),已废弃。
MAX_SILENCE_LENGTH = 3.0  # 超过该时长的静音才切除
START_SILENCE_KEEP = 0.02
END_SILENCE_KEEP = 0.02
FADE_LENGTH = 0.5  # 已废弃,仅为兼容旧导出保留(vh_core.postproc.silence re-export)

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v"}

try:
    import librosa
    import numpy as np

    _LIBROSA_OK = True
except ImportError:  # pragma: no cover
    _LIBROSA_OK = False


def _extract_audio(src: Path, wav: Path, ffmpeg: str | None = None) -> None:
    run_ffmpeg(
        ["-i", str(src), "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", str(wav)],
        desc="静音检测-提取音频",
        ffmpeg=ffmpeg,
    )


def _detect_silence_librosa(audio_path: Path, noise_db: float, min_dur: float) -> list[dict]:
    """RMS 能量 + 零交叉率 + 频谱质心综合判定。"""
    audio_data, sr = librosa.load(str(audio_path), sr=None)
    frame_length = int(0.025 * sr)  # 25ms 帧长
    hop_length = int(0.01 * sr)  # 10ms 跳跃

    rms = librosa.feature.rms(y=audio_data, frame_length=frame_length, hop_length=hop_length)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=1.0)
    zcr = librosa.feature.zero_crossing_rate(audio_data, frame_length=frame_length, hop_length=hop_length)[0]
    centroids = librosa.feature.spectral_centroid(y=audio_data, sr=sr, hop_length=hop_length)[0]

    low_energy = rms_db < noise_db
    low_zcr = zcr < np.percentile(zcr, 30)
    low_centroid = centroids < np.percentile(centroids, 30)
    near_threshold = rms_db < (noise_db + 10)
    silence_frames = low_energy | (near_threshold & low_zcr & low_centroid)

    frame_times = librosa.frames_to_time(np.arange(len(silence_frames)), sr=sr, hop_length=hop_length)
    return _frames_to_segments(silence_frames, frame_times, hop_length / sr, min_dur)


def _detect_silence_numpy(wav_path: Path, noise_db: float, min_dur: float) -> list[dict]:
    """无 librosa 时的回退:stdlib wave + numpy 纯 RMS 能量门限。"""
    import numpy as np

    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    frame_length = int(0.025 * sr)
    hop_length = int(0.01 * sr)
    n_frames = 1 + max(0, (len(samples) - frame_length) // hop_length)
    rms = np.array(
        [
            np.sqrt(np.mean(samples[i * hop_length : i * hop_length + frame_length] ** 2) + 1e-12)
            for i in range(n_frames)
        ]
    )
    rms_db = 20 * np.log10(rms + 1e-12)
    silence_frames = rms_db < noise_db
    frame_times = np.arange(n_frames) * hop_length / sr
    return _frames_to_segments(silence_frames, frame_times, hop_length / sr, min_dur)


def _frames_to_segments(silence_frames, frame_times, hop_sec: float, min_dur: float) -> list[dict]:
    """帧级布尔序列 → 连续静音段 [{start, end, duration}](毫秒精度)。"""
    segments: list[dict] = []
    in_silence = False
    start_ms: int | None = None
    for is_silent, t in zip(silence_frames, frame_times):
        now_ms = int(t * 1000)
        if is_silent and not in_silence:
            start_ms = now_ms
            in_silence = True
        elif not is_silent and in_silence:
            dur_ms = now_ms - start_ms
            if dur_ms >= min_dur * 1000:
                segments.append({"start": start_ms / 1000.0, "end": now_ms / 1000.0, "duration": dur_ms / 1000.0})
            in_silence = False
    if in_silence and start_ms is not None:
        end_ms = int((frame_times[-1] + hop_sec) * 1000)
        dur_ms = end_ms - start_ms
        if dur_ms >= min_dur * 1000:
            segments.append({"start": start_ms / 1000.0, "end": end_ms / 1000.0, "duration": dur_ms / 1000.0})
    return segments


def detect_silences(
    src: Path, work_dir: Path, min_dur: float = MAX_SILENCE_LENGTH, noise_db: float = -30.0, ffmpeg: str | None = None
) -> list[dict]:
    """检测音视频中的静音段,结果缓存到 work_dir(JSON)。"""
    src, work_dir = Path(src), Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    # 缓存键带文件大小+mtime:同名文件(process 流程固定叫 step0_src)内容变了
    # 不会误读旧缓存,避免按上一段视频的静音位置错切
    st = src.stat()
    cache = work_dir / f"{src.stem}.sc{min_dur}n{noise_db}.{st.st_size}x{int(st.st_mtime)}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(f"静音缓存读取失败,重新检测: {cache}")

    wav = work_dir / f"{src.stem}_detect.wav"
    if src.suffix.lower() in VIDEO_EXTS:
        _extract_audio(src, wav, ffmpeg=ffmpeg)
        audio = wav
    else:
        audio = src

    try:
        if _LIBROSA_OK:
            segments = _detect_silence_librosa(audio, noise_db, min_dur)
        else:
            logger.warning("librosa 不可用,回退 numpy RMS 静音检测")
            if audio.suffix.lower() != ".wav":
                _extract_audio(audio, wav, ffmpeg=ffmpeg)
                audio = wav
            segments = _detect_silence_numpy(audio, noise_db, min_dur)
    finally:
        if audio == wav and wav.exists():
            wav.unlink()

    for i, s in enumerate(segments):
        logger.debug(f"静音段 {i + 1}: {s['start']:.3f}s - {s['end']:.3f}s (时长 {s['duration']:.3f}s)")
    cache.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    return segments


def cut_silence(
    video: Path,
    output: Path,
    work_dir: Path,
    noise_db: float = -30.0,
    min_dur: float = MAX_SILENCE_LENGTH,
    ffmpeg: str | None = None,
) -> Path:
    """切除静音段并硬切拼接(切点位于静音内 20ms 处,无叠化)。
    video/output 均须位于 work_dir 内(内部以相对路径 + cwd=work_dir 执行,
    绕开 Windows 盘符滤镜转义)。无静音段时直接复制,返回 output。"""
    video = Path(video)
    output = Path(output)
    work_dir = Path(work_dir)
    exe = ffmpeg or get_ffmpeg()
    duration, _, _ = probe_video(video, ffmpeg=exe)
    silences = detect_silences(video, work_dir, min_dur, noise_db, ffmpeg=exe)

    if not silences:
        logger.info("未检测到可切除的静音段,原样保留")
        shutil.copyfile(video, output)
        return output

    # 保留段 [start, end):静音头尾各留 20ms,段间直接硬切
    keeps: list[tuple[float, float]] = []
    prev = 0.0
    for silence in silences:
        end_ts = (int(silence["start"] * 1000) + int(START_SILENCE_KEEP * 1000)) / 1000.0
        keeps.append((prev, end_ts))
        prev = (int(silence["end"] * 1000) - int(END_SILENCE_KEEP * 1000)) / 1000.0
    keeps.append((prev, duration))

    ss = _StartStream()
    combined = _InputStream(ss)
    streams = [_PtsTrim(combined, start=a, end=b) for a, b in keeps if b - a > 0.001]
    if not streams:
        logger.warning("静音切除后无有效保留段,原样保留")
        shutil.copyfile(video, output)
        return output

    concat = _Concat(*streams)
    filter_list: list[str] = []
    concat.filters(filter_list)
    cfs = work_dir / f"{video.stem}.cfs"
    with open(cfs, mode="w", encoding="utf-8") as f:
        f.write(";".join(filter_list))

    run_ffmpeg(
        [
            "-i",
            video.name,
            "-filter_complex_script",
            cfs.name,
            "-map",
            concat.video_id,
            "-map",
            concat.audio_id,
            "-c:v",
            "libx264",
            "-preset",
            "superfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            output.name,
        ],
        desc="静音切除",
        cwd=work_dir,
        ffmpeg=exe,
    )
    cfs.unlink(missing_ok=True)
    return output


class _BaseStream:
    def __init__(self, stream_id: int):
        self.stream_id = stream_id
        self.video_id = f"[v{stream_id}]"
        self.audio_id = f"[a{stream_id}]"

    def next_id(self) -> int:
        raise NotImplementedError


class _StartStream(_BaseStream):
    def __init__(self):
        super().__init__(-1)
        self._next = self.stream_id

    def next_id(self) -> int:
        self._next += 1
        return self._next

    def filters(self, filter_list: list[str]) -> None:
        pass


class _InputStream(_BaseStream):
    def __init__(self, src: _StartStream, audio_stream_id: int = 0):
        self.src = src
        self.stream_id = src.next_id()
        super().__init__(self.stream_id)
        self.video_id = f"[{self.stream_id}:v:0]"
        self.audio_id = f"[{self.stream_id}:a:{audio_stream_id}]"

    def next_id(self) -> int:
        return self.src.next_id()

    def filters(self, filter_list: list[str]) -> None:
        self.src.filters(filter_list)


class _SubStream(_BaseStream):
    def __init__(self, src):
        self.src = src
        super().__init__(src.next_id())

    def next_id(self) -> int:
        return self.src.next_id()

    def filters(self, filter_list: list[str]) -> None:
        self.src.filters(filter_list)


class _PtsTrim(_SubStream):
    def __init__(self, src, **kwargs):
        super().__init__(src)
        self.kwargs = kwargs

    def filters(self, filter_list: list[str]) -> None:
        self.src.filters(filter_list)
        parms = ":".join(f"{k}={v}" for k, v in self.kwargs.items())
        filter_list.extend(
            [
                f"{self.src.video_id}trim={parms},setpts=PTS-STARTPTS{self.video_id}",
                f"{self.src.audio_id}atrim={parms},asetpts=PTS-STARTPTS{self.audio_id}",
            ]
        )

    def duration(self) -> float:
        return self.kwargs["end"] - self.kwargs["start"]


class _Concat(_BaseStream):
    def __init__(self, *streams):
        self.streams = streams
        super().__init__(streams[0].next_id())

    def next_id(self) -> int:
        raise NotImplementedError

    def filters(self, filter_list: list[str]) -> None:
        ids: list[str] = []
        for s in self.streams:
            s.filters(filter_list)
            ids.extend([s.video_id, s.audio_id])
        filter_list.append(f"{''.join(ids)}concat=n={len(self.streams)}:v=1:a=1{self.video_id}{self.audio_id}")


# ------------------------------------------------------------------ 封面


def extract_frame(video: Path, output: Path, frame_time: float | None = None, ffmpeg: str | None = None) -> Path:
    """抽视频一帧做封面底图;frame_time 缺省取时长中点。"""
    video, output = Path(video), Path(output)
    exe = ffmpeg or get_ffmpeg()
    if frame_time is None:
        duration, _, _ = probe_video(video, ffmpeg=exe)
        frame_time = duration / 2
    run_ffmpeg(
        ["-ss", str(frame_time), "-i", str(video), "-vframes", "1", "-q:v", "2", str(output)],
        desc="封面抽帧",
        ffmpeg=exe,
    )
    return output


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """支持 #RRGGBB / rgb() / rgba(),解析失败回退黑色。"""
    c = color.strip()
    try:
        if c.startswith("rgb"):
            vals = c[c.index("(") + 1 : c.index(")")].split(",")
            return tuple(int(float(v.strip())) for v in vals[:3])  # type: ignore[return-value]
        c = c.lstrip("#")
        if len(c) != 6:
            return (0, 0, 0)
        return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except (ValueError, IndexError):
        return (0, 0, 0)


def _split_highlights(
    line: str,
    highlight_words: list[str],
    font_color: str,
    highlight_color: str,
) -> list[tuple[str, str]]:
    """把一行文案按高亮词切成 [(片段, 颜色)];高亮词按长度降序替换为标记,
    避免短词抢先匹配。"""
    markers: dict[str, str] = {}
    processed = line
    for idx, word in enumerate(sorted(highlight_words, key=len, reverse=True)):
        if not word.strip():
            continue
        marker = f"__HL_{idx}__"
        processed = processed.replace(word, marker)
        markers[marker] = word

    segments: list[tuple[str, str]] = []
    buf = ""
    i = 0
    while i < len(processed):
        hit = next((m for m in markers if processed.startswith(m, i)), None)
        if hit:
            if buf:
                segments.append((buf, font_color))
                buf = ""
            segments.append((markers[hit], highlight_color))
            i += len(hit)
        else:
            buf += processed[i]
            i += 1
    if buf:
        segments.append((buf, font_color))
    return [(seg, color) for seg, color in segments if seg]


def add_multicolor_text(
    image_path: Path,
    output_path: Path,
    first_line: str,
    second_line: str,
    highlight_words: list[str] | None,
    font_path: Path,
    font_size: int = 90,
    font_color: str = "#FFFFFF",
    highlight_color: str = "#FFD600",
    outline_color: str = "#000000",
    outline_size: int = 4,
    position: str = "bottom",
) -> Path:
    """在封面底图上画两行(可单行)多色文字,高亮词分色,整体水平居中。
    first_line/second_line 都为空时原样复制底图到 output_path。"""
    from PIL import Image, ImageDraw, ImageFont

    image_path, output_path = Path(image_path), Path(output_path)
    if not first_line.strip() and not second_line.strip():
        output_path.write_bytes(image_path.read_bytes())
        return output_path

    image = Image.open(image_path)
    draw = ImageDraw.Draw(image)
    w, h = image.size
    font = ImageFont.truetype(str(font_path), font_size)

    words = highlight_words or []
    lines: list[list[tuple[str, str]]] = []
    for line in (first_line, second_line):
        if line.strip():
            lines.append(_split_highlights(line, words, font_color, highlight_color))

    line_height = font_size * 1.2
    line_widths = [sum(draw.textlength(seg, font=font) for seg, _ in line) for line in lines]
    total_height = len(lines) * line_height
    if position == "top":
        y = h * 0.1
    elif position == "center":
        y = (h - total_height) / 2
    else:  # bottom
        y = h * 0.78 - total_height

    outline_rgb = _hex_to_rgb(outline_color)
    for idx, line in enumerate(lines):
        x = (w - line_widths[idx]) / 2
        for seg, color in line:
            for dx in range(-outline_size, outline_size + 1):
                for dy in range(-outline_size, outline_size + 1):
                    if dx or dy:
                        draw.text((x + dx, y + dy), seg, font=font, fill=outline_rgb)
            draw.text((x, y), seg, font=font, fill=_hex_to_rgb(color))
            x += draw.textlength(seg, font=font)
        y += line_height

    image.save(output_path)
    return output_path


# ------------------------------------------------------------------ 水印

FONT_EXTS = {".ttf", ".otf", ".ttc"}

_POSITIONS = {
    "top_left": "x={m}:y={m}",
    "top_right": "x=w-text_w-{m}:y={m}",
    "bottom_left": "x={m}:y=h-text_h-{m}",
    "bottom_right": "x=w-text_w-{m}:y=h-text_h-{m}",
}


def _has_glyphs(font_file: Path, text: str) -> bool:
    """检查字体 cmap 是否覆盖 text 的全部字符(艺术字库字符集不全时
    drawtext 会渲染成豆腐块)。"""
    from fontTools.ttLib import TTFont

    tt = TTFont(str(font_file), fontNumber=0, lazy=True)
    try:
        cmap = tt.getBestCmap() or {}
        return all(ord(c) in cmap for c in text if not c.isspace())
    finally:
        tt.close()


def pick_font_file(font_dir: Path, preferred: str = "", sample_text: str = "") -> Path | None:
    """从字体目录挑一个可用字体文件(fontTools 验证可读)。优先 .ttf/.otf
    (drawtext 与 PIL 对 .ttc 支持都不如单文件字体稳);
    给了 sample_text 时跳过字形覆盖不全的字体。"""
    from fontTools.ttLib import TTFont

    font_dir = Path(font_dir)
    files: list[Path] = []
    if preferred:
        p = font_dir / preferred if not Path(preferred).is_absolute() else Path(preferred)
        if p.exists():
            files = [p]
    if not files and font_dir.is_dir():
        all_fonts = sorted(f for f in font_dir.iterdir() if f.suffix.lower() in FONT_EXTS)
        files = [f for f in all_fonts if f.suffix.lower() != ".ttc"] + [
            f for f in all_fonts if f.suffix.lower() == ".ttc"
        ]
    for f in files:
        try:
            tt = TTFont(str(f), fontNumber=0, lazy=True)
            name = tt["name"].getDebugName(1)
            tt.close()
            if not name:
                continue
            if sample_text and not _has_glyphs(f, sample_text):
                logger.debug(f"字体 {f.name} 字库不覆盖 {sample_text!r},跳过")
                continue
            return f
        except Exception:
            continue
    return None


def add_text_watermark(
    video: Path,
    output: Path,
    work_dir: Path,
    text: str,
    font_dir: Path,
    font_size: int = 60,
    font_color: str = "white",
    position: str = "top_right",
    margin: int = 40,
    ffmpeg: str | None = None,
) -> Path:
    """给视频右上角(默认)加文字水印,音频直拷。video/output 须位于 work_dir。
    drawtext 用 textfile= 方案(文本写临时文件)绕开转义;fontfile 用相对
    work_dir 的路径,绕开 Windows 盘符冒号被当滤镜分隔符的问题。"""
    video, output, work_dir = Path(video), Path(output), Path(work_dir)
    font = pick_font_file(font_dir, sample_text=text)
    if font is None:
        raise RuntimeError(f"字体目录无可用字体: {font_dir}")

    # drawtext 的 fontfile 路径含中文时(经滤镜字符串解析)字体会静默加载失败
    # 渲染成豆腐块;复制为 ASCII 文件名到 work_dir 规避
    font_local = work_dir / f"wm_font{font.suffix.lower()}"
    shutil.copyfile(font, font_local)

    text_file = work_dir / "watermark.txt"
    text_file.write_text(text, encoding="utf-8")
    coords = _POSITIONS.get(position, _POSITIONS["top_right"]).format(m=margin)
    vf = (
        f"drawtext=textfile='{text_file.name}':fontfile='{font_local.name}':"
        f"fontsize={font_size}:fontcolor={font_color}:{coords}:"
        f"box=1:boxcolor=black@0.3:boxborderw=5"
    )

    run_ffmpeg(
        [
            "-i",
            video.name,
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "superfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            output.name,
        ],
        desc="视频水印",
        cwd=work_dir,
        ffmpeg=ffmpeg,
    )
    text_file.unlink(missing_ok=True)
    font_local.unlink(missing_ok=True)
    return output


def add_ai_watermark(
    image_path: Path,
    output_path: Path,
    text: str = "此封面为AI生成",
    font_dir: Path | None = None,
    font_size: int = 40,
    font_color: str = "white",
    position: str = "top_right",
    margin: int = 30,
) -> Path:
    """PIL 给封面图打 AI 标识(黑描边白字),写 output_path,不改原图。
    找不到字体时用 PIL 默认字体兜底。"""
    from PIL import Image, ImageDraw, ImageFont

    image_path, output_path = Path(image_path), Path(output_path)
    image = Image.open(image_path)
    draw = ImageDraw.Draw(image)

    font = None
    if font_dir is not None:
        font_file = pick_font_file(font_dir, sample_text=text)
        if font_file is not None:
            try:
                font = ImageFont.truetype(str(font_file), font_size)
            except Exception as e:
                logger.warning(f"AI 标识字体加载失败({e}),用默认字体")
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    w, h = image.size
    pos_map = {
        "top_left": (margin, margin),
        "top_right": (w - tw - margin, margin),
        "bottom_left": (margin, h - th - margin),
        "bottom_right": (w - tw - margin, h - th - margin),
    }
    x, y = pos_map.get(position, pos_map["top_right"])

    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill="black")
    draw.text((x, y), text, font=font, fill=font_color)

    image.save(output_path)
    return output_path


# ------------------------------------------------------------------ 字幕

# 句末标点集合:字幕分句后要把句尾的标点串(如 "。。。")整段剥掉
_TRAILING_PUNCT = ",。!?!;;"


def _strip_trailing_punct(seg: str) -> str:
    """剥掉句尾连续的标点(参数是字符集,与 str.strip 语义不同,故单列成函数)。"""
    return seg.rstrip(_TRAILING_PUNCT)


def _ass_ts(t: float) -> str:
    """秒 → ASS 时间戳 h:mm:ss.cc。"""
    ms = round(t * 1000)
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, cs = divmod(ms, 1000)
    return f"{h}:{m:02d}:{s:02d}.{cs // 10:02d}"


def split_subtitle_lines(text: str, max_len: int = 15) -> list[str]:
    """把文案切成适合单行显示的短句:先按行,再按标点,超长再硬切。"""
    chunks: list[str] = []
    for raw in text.splitlines():
        for seg in re.split(r"(?<=[,。!?!;;])", raw.strip()):
            seg = _strip_trailing_punct(seg)
            if not seg:
                continue
            while len(seg) > max_len:
                chunks.append(seg[:max_len])
                seg = seg[max_len:]
            chunks.append(seg)
    return chunks


def even_subtitle_segments(lines: Sequence[str], duration: float) -> list[dict]:
    """均分时间轴的字幕 segments:[{text, start, end}](秒)。"""
    if not lines:
        return []
    per = duration / len(lines)
    return [{"text": line, "start": i * per, "end": (i + 1) * per} for i, line in enumerate(lines)]


def write_ass(segments: list[dict], out: Path, font_name: str, fontsize: int, width: int, height: int) -> None:
    """生成 ASS 字幕:显式 PlayRes 匹配视频分辨率(libass 对 SRT 用 384x288
    默认画布,FontSize 会被放大数倍),底部居中对齐。
    segments 为 [{text, start, end}](秒),时间轴由调用方给出。"""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{fontsize},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,20,20,60,134

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = "".join(
        f"Dialogue: 0,{_ass_ts(seg['start'])},{_ass_ts(seg['end'])},Default,,0,0,0,,{seg['text']}\n" for seg in segments
    )
    Path(out).write_text(header + events, encoding="utf-8")


def _split_cover_text(text: str) -> tuple[str, str]:
    """ "第一行,第二行" → (第一行, 第二行),按首个中英文逗号切。"""
    for sep in ("，", ","):
        if sep in text:
            first, second = text.split(sep, 1)
            return first.strip(), second.strip()
    return text.strip(), ""


def _font_internal_name(font_file: Path) -> str | None:
    """fontTools 读字体内部名,供 libass 匹配。"""
    from fontTools.ttLib import TTFont

    try:
        tt = TTFont(str(font_file), fontNumber=0, lazy=True)
        name = tt["name"].getDebugName(1)
        tt.close()
        return name or None
    except Exception:
        return None


# ------------------------------------------------------------------ 编排


def process_video(
    input_video: Path,
    out_dir: Path,
    text: str = "",
    bgm: Path | None = None,
    font: Path | None = None,
    bg_image: Path | None = None,
    watermark_text: str | None = None,
    cover_text: str = "",
    cover_highlights: Sequence[str] = (),
    silence_cut: bool = True,
    subtitle: bool = True,
    bgm_enabled: bool = True,
    cover: bool = True,
    bgm_volume: float = 0.15,
    subtitle_fontsize: int = 44,
    silence_noise_db: float = -30.0,
    silence_min_dur: float = MAX_SILENCE_LENGTH,
    bg_canvas_w: int = 1080,
    bg_canvas_h: int = 1920,
    bg_fg_ratio: float = 0.9,
    cover_ai_text: str = "此封面为AI生成",
    ffmpeg: str | None = None,
) -> dict[str, Path]:
    """六步编排:静音切除 → 背景图合成 → 字幕烧录(均分) → BGM 混音 → 水印 → 封面。

    所有素材路径显式传入,没给就跳过对应步骤;产物 final.mp4 / cover.png /
    sub.ass 放 out_dir,中间文件用完清理。返回 {"final": ..., "cover": ..., ...}。
    """
    input_video, out_dir = Path(input_video), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = ffmpeg or get_ffmpeg()
    # ffmpeg 以 cwd=out_dir 执行,外部传入的素材路径统一转绝对
    input_video = input_video.resolve()
    if bgm is not None:
        bgm = Path(bgm).resolve()
    if font is not None:
        font = Path(font).resolve()
    if bg_image is not None:
        bg_image = Path(bg_image).resolve()

    cur = out_dir / "step0_src.mp4"
    shutil.copyfile(input_video, cur)  # 统一在 out_dir 内相对路径操作

    # 字体:显式路径;给目录时从中挑一个可用字体
    font_file: Path | None = None
    if font is not None:
        font = Path(font)
        if font.is_dir():
            font_file = pick_font_file(font, sample_text=text + cover_text + cover_ai_text)
        elif font.exists():
            font_file = font
        if font_file is None:
            logger.warning(f"字体不可用({font}),字幕/水印/封面文字相关步骤将受影响")

    # 1. 静音切除
    if silence_cut:
        try:
            nxt = out_dir / "step1_silence.mp4"
            cut_silence(cur, nxt, out_dir, noise_db=silence_noise_db, min_dur=silence_min_dur, ffmpeg=exe)
            cur = nxt
        except Exception as e:
            logger.warning(f"静音切除失败,保留原视频: {e}")

    # 2. 背景图合成(显式单张图片路径)
    if bg_image is not None:
        bg = Path(bg_image)
        if not bg.exists():
            logger.warning(f"背景图不存在({bg}),跳过背景图合成")
        else:
            fw = int(bg_canvas_w * bg_fg_ratio)
            nxt = out_dir / "step2_bg.mp4"
            run_ffmpeg(
                [
                    "-i",
                    cur.name,
                    "-loop",
                    "1",
                    "-i",
                    str(bg),
                    "-filter_complex",
                    f"[1:v]scale={bg_canvas_w}:{bg_canvas_h}[bg];[0:v]scale={fw}:-2[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v]",
                    "-map",
                    "[v]",
                    "-map",
                    "0:a",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "superfast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    nxt.name,
                ],
                desc="背景图合成",
                cwd=out_dir,
                ffmpeg=exe,
            )
            cur = nxt

    # 封面底图来源:背景图合成之后、字幕烧录之前的视频(无字幕帧)
    cover_src = cur

    # 3. 字幕烧录(均分时间轴)
    if subtitle:
        lines = split_subtitle_lines(text) if text.strip() else []
        if not lines:
            logger.warning("无文案文本,跳过字幕烧录")
        elif font_file is None:
            logger.warning("未提供字体,跳过字幕烧录")
        else:
            font_name = _font_internal_name(font_file)
            if not font_name:
                logger.warning(f"字体内部名读取失败({font_file}),跳过字幕烧录")
            else:
                duration, vw, vh = probe_video(cur, ffmpeg=exe)
                segments = even_subtitle_segments(lines, duration)
                write_ass(segments, out_dir / "sub.ass", font_name, subtitle_fontsize, vw, vh)
                fontsdir = os.path.relpath(font_file.parent, out_dir).replace(os.sep, "/")
                nxt = out_dir / "step3_subtitled.mp4"
                run_ffmpeg(
                    ["-i", cur.name, "-vf", f"subtitles=sub.ass:fontsdir='{fontsdir}'", "-c:a", "copy", nxt.name],
                    desc="字幕烧录",
                    cwd=out_dir,
                    ffmpeg=exe,
                )
                cur = nxt

    # 4. BGM 混音
    if bgm_enabled:
        if bgm is None or not Path(bgm).exists():
            logger.warning("未提供 BGM(或路径不存在),输出仅含原音")
        else:
            nxt = out_dir / "step4_bgm.mp4"
            run_ffmpeg(
                [
                    "-i",
                    cur.name,
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(bgm),
                    "-filter_complex",
                    f"[1:a]volume={bgm_volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first[a]",
                    "-map",
                    "0:v",
                    "-map",
                    "[a]",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    nxt.name,
                ],
                desc="BGM 混音",
                cwd=out_dir,
                ffmpeg=exe,
            )
            cur = nxt

    # 5. 视频水印
    if watermark_text:
        if font_file is None:
            logger.warning("未提供字体,跳过视频水印")
        else:
            try:
                nxt = out_dir / "step5_watermark.mp4"
                add_text_watermark(cur, nxt, out_dir, str(watermark_text), font_dir=font_file.parent, ffmpeg=exe)
                cur = nxt
            except Exception as e:
                logger.warning(f"视频水印失败,保留无水印版本: {e}")

    # 6. 封面生成:抽中点帧,可选多色文字 + AI 标识
    outputs: dict[str, Path] = {}
    if cover:
        try:
            raw = out_dir / "cover_raw.png"
            extract_frame(cover_src, raw, ffmpeg=exe)
            img = raw
            if cover_text.strip() and font_file is not None:
                first, second = _split_cover_text(cover_text)
                out_text = out_dir / "cover_text.png"
                add_multicolor_text(raw, out_text, first, second, list(cover_highlights), font_file)
                img = out_text
            elif cover_text.strip():
                logger.warning("未提供字体,封面不画文字")
            cover_path = out_dir / "cover.png"
            add_ai_watermark(img, cover_path, text=cover_ai_text, font_dir=font_file.parent if font_file else None)
            raw.unlink(missing_ok=True)
            (out_dir / "cover_text.png").unlink(missing_ok=True)
            outputs["cover"] = cover_path
        except Exception as e:
            logger.warning(f"封面生成失败: {e}")

    final = out_dir / "final.mp4"
    if cur.name != final.name:
        shutil.copyfile(cur, final)
    outputs["final"] = final
    if (out_dir / "sub.ass").exists():
        outputs["subtitle"] = out_dir / "sub.ass"

    # 清理中间文件
    for f in out_dir.glob("step*.mp4"):
        f.unlink()
    for f in out_dir.glob("*.sc*n*.json"):
        f.unlink()
    return outputs
