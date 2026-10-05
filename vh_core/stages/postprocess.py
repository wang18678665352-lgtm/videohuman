"""postprocess stage — 六步编排:静音切除 → 背景图合成 → 字幕烧录(ASR 对齐)
→ BGM 混音 → 视频水印;封面在第 2 步后、字幕前抽帧生成。

每步可用 params["postprocess"] 的键开关(默认值见各步注释),前一步输出
是后一步输入,中间文件 stepN_*.mp4 落 stage_dir,结束后清理。
命令在 stage_dir 内以相对路径执行,绕开 Windows 盘符滤镜转义问题。
"""

from __future__ import annotations

import os
import random
import re
import subprocess
from pathlib import Path

from loguru import logger

from ..config import Settings
from ..ffmpeg_utils import get_ffmpeg, run_ffmpeg
from ..pipeline import Stage, StageContext
from ..postproc import (
    add_ai_watermark,
    add_multicolor_text,
    add_text_watermark,
    cut_silence,
    extract_frame,
    pick_font_file,
)

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
FONT_EXTS = {".ttf", ".otf", ".ttc"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _video_info(settings: Settings, video: Path) -> tuple[float, int, int]:
    """返回 (时长秒, 宽, 高)。"""
    exe = get_ffmpeg(settings)
    r = subprocess.run([exe, "-hide_banner", "-i", str(video)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    err = r.stderr or ""
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if not m:
        raise RuntimeError(f"无法读取视频时长: {video}")
    h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    duration = h * 3600 + mnt * 60 + s
    dm = re.search(r"Stream .*?Video:.*? (\d{2,5})x(\d{2,5})", err)
    if not dm:
        raise RuntimeError(f"无法读取视频分辨率: {video}")
    return duration, int(dm.group(1)), int(dm.group(2))


def _ass_ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, cs = divmod(ms, 1000)
    return f"{h}:{m:02d}:{s:02d}.{cs // 10:02d}"


def split_subtitle_lines(text: str, max_len: int = 15) -> list[str]:
    """把文案切成适合单行显示的短句:先按行,再按标点,超长再硬切。"""
    chunks: list[str] = []
    for raw in text.splitlines():
        for seg in re.split(r"(?<=[,。!?!;;])", raw.strip()):
            seg = seg.strip().rstrip(",。!?!;;")
            if not seg:
                continue
            while len(seg) > max_len:
                chunks.append(seg[:max_len])
                seg = seg[max_len:]
            chunks.append(seg)
    return chunks


def _write_ass(segments: list[dict], out: Path,
               font_name: str, fontsize: int, width: int, height: int) -> None:
    """生成 ASS 字幕:显式 PlayRes 匹配视频分辨率(libass 对 SRT 用 384x288
    默认画布,FontSize 会被放大数倍),底部居中对齐。
    segments 为 [{text, start, end}](秒),时间轴由调用方给出
    (ASR 词级对齐或均分时长)。"""
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
        f"Dialogue: 0,{_ass_ts(seg['start'])},{_ass_ts(seg['end'])},"
        f"Default,,0,0,0,,{seg['text']}\n"
        for seg in segments
    )
    out.write_text(header + events, encoding="utf-8")


def _pick_bgm(settings: Settings, ctx: StageContext) -> Path | None:
    cand = ctx.params.get("bgm_path") or settings.bgm_path
    if cand:
        p = settings.abs_path(Path(cand))
        return p if p.exists() else None
    bgm_dir = settings.assets / "bgm"
    if bgm_dir.is_dir():
        for f in sorted(bgm_dir.iterdir()):
            if f.suffix.lower() in AUDIO_EXTS:
                return f
    return None


def _pick_font(settings: Settings, ctx: StageContext) -> tuple[Path, str] | None:
    """返回 (字体文件, 字体内部名)。fontTools 读内部名供 libass 匹配。"""
    from fontTools.ttLib import TTFont  # noqa: PLC0415

    cand = ctx.params.get("subtitle_font") or settings.subtitle_font
    font_dir = settings.assets / "font"
    files: list[Path] = []
    if cand:
        p = settings.abs_path(Path(cand))
        if p.exists():
            files = [p]
    if not files and font_dir.is_dir():
        files = sorted(f for f in font_dir.iterdir() if f.suffix.lower() in FONT_EXTS)
    for f in files:
        try:
            tt = TTFont(str(f), fontNumber=0, lazy=True)
            name = tt["name"].getDebugName(1)
            if name:
                return f, name
        except Exception:
            continue
    return None


def _pick_bg_image(settings: Settings, bg_opt) -> Path | None:
    """bg_image 参数:True=背景图目录随机一张;字符串=目录内图片名或路径。"""
    bg_dir = settings.assets / "background_images"
    if isinstance(bg_opt, str):
        for p in (bg_dir / bg_opt, settings.abs_path(Path(bg_opt))):
            if p.exists():
                return p
        return None
    if bg_dir.is_dir():
        files = [f for f in sorted(bg_dir.iterdir()) if f.suffix.lower() in IMAGE_EXTS]
        if files:
            return random.choice(files)
    return None


def _split_cover_text(text: str) -> tuple[str, str]:
    """"第一行,第二行" → (第一行, 第二行),按首个中英文逗号切。"""
    for sep in ("，", ","):
        if sep in text:
            first, second = text.split(sep, 1)
            return first.strip(), second.strip()
    return text.strip(), ""


def _align_lines_to_asr(lines: list[str], words: list[str],
                        timestamps: list[list[float]], duration: float) -> list[dict]:
    """把**原文**分句对齐到 ASR 词级时间轴:文本保真(不用 ASR 识别文本),
    只借时间戳。timestamps 单位毫秒(SenseVoice)。对齐失败返回空列表(调用方回退)。"""
    import difflib

    if not lines or not words or not timestamps:
        return []
    asr_full = "".join(words)
    # 每个 ASR 字符的 (start, end),词内线性插值
    c_start: list[float] = []
    c_end: list[float] = []
    for w, (ws, we) in zip(words, timestamps):
        n = max(len(w), 1)
        for i in range(len(w)):
            c_start.append((ws + (we - ws) * i / n) / 1000)
            c_end.append((ws + (we - ws) * (i + 1) / n) / 1000)

    # 原文(去标点后的拼接)字符位置 → ASR 字符位置
    orig_full = "".join(lines)
    matcher = difflib.SequenceMatcher(None, orig_full, asr_full, autojunk=False)
    orig_to_asr: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            orig_to_asr[block.a + k] = block.b + k
    if len(orig_to_asr) < len(orig_full) * 0.5:
        logger.warning(f"原文与 ASR 文本匹配率过低({len(orig_to_asr)}/{len(orig_full)}),回退均分")
        return []

    segments: list[dict] = []
    offset = 0
    for line in lines:
        span = range(offset, offset + len(line))
        times = [(c_start[orig_to_asr[j]], c_end[orig_to_asr[j]])
                 for j in span if j in orig_to_asr]
        if times:
            segments.append({"text": line,
                             "start": round(min(t[0] for t in times), 2),
                             "end": round(max(t[1] for t in times), 2)})
        else:
            segments.append({"text": line, "start": None, "end": None})
        offset += len(line)

    # 未匹配的句:在相邻句之间插值
    for i, seg in enumerate(segments):
        if seg["start"] is not None:
            continue
        prev_end = next((segments[j]["end"] for j in range(i - 1, -1, -1)
                         if segments[j]["start"] is not None), 0.0)
        next_start = next((segments[j]["start"] for j in range(i + 1, len(segments))
                           if segments[j]["start"] is not None), duration)
        seg["start"] = round(prev_end, 2)
        seg["end"] = round(next_start, 2)
    # 相邻句缝隙处理:后句 start 不早于前句 end
    for i in range(1, len(segments)):
        if segments[i]["start"] < segments[i - 1]["end"]:
            segments[i]["start"] = segments[i - 1]["end"]
    return segments


class PostprocessStage(Stage):
    name = "postprocess"

    def run(self, ctx: StageContext) -> dict[str, Path]:
        s = ctx.settings
        src = ctx.job_dir / "avatar" / "avatar.mp4"
        if not src.exists():
            raise FileNotFoundError("postprocess 需要 avatar 产物 avatar.mp4")

        pp = dict(ctx.params.get("postprocess", {}))
        cur = ctx.stage_dir / "step0_src.mp4"
        cur.write_bytes(src.read_bytes())  # 统一在 stage_dir 内相对路径操作
        silence_applied = False

        # 1. 静音切除 silence_cut(默认 True)
        if pp.get("silence_cut", True):
            try:
                nxt = ctx.stage_dir / "step1_silence.mp4"
                cut_silence(s, cur, nxt, ctx.stage_dir,
                            noise_db=float(pp.get("silence_noise_db", -30.0)),
                            min_dur=float(pp.get("silence_min_dur", 3.0)))
                cur = nxt
                silence_applied = True
            except Exception as e:
                logger.warning(f"静音切除失败,保留原视频: {e}")

        # 2. 背景图合成 bg_image(默认 False;True=随机,字符串=图片名/路径)
        bg_opt = pp.get("bg_image", False)
        if bg_opt:
            bg = _pick_bg_image(s, bg_opt)
            if bg is None:
                logger.warning(f"背景图不可用({bg_opt}),跳过背景图合成")
            else:
                cw = int(pp.get("bg_canvas_w", 1080))
                ch = int(pp.get("bg_canvas_h", 1920))
                fw = int(cw * float(pp.get("bg_fg_ratio", 0.9)))
                nxt = ctx.stage_dir / "step2_bg.mp4"
                run_ffmpeg(s, [
                    "-i", cur.name, "-loop", "1", "-i", str(bg),
                    "-filter_complex",
                    f"[1:v]scale={cw}:{ch}[bg];[0:v]scale={fw}:-2[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v]",
                    "-map", "[v]", "-map", "0:a",
                    "-c:v", "libx264", "-preset", "superfast", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", nxt.name,
                ], desc="背景图合成", cwd=ctx.stage_dir)
                cur = nxt

        # 封面底图来源:第 2 步之后、字幕烧录之前的视频(无字幕帧)
        cover_src = cur

        # 3. 字幕烧录 subtitle(默认 True;ASR 时间轴优先,失败回退均分)
        if pp.get("subtitle", True):
            duration, vw, vh = _video_info(s, cur)
            segments = self._subtitle_segments(ctx, cur, duration, silence_applied)
            font = _pick_font(s, ctx)
            if segments and font:
                font_file, font_name = font
                fontsize = int(pp.get("subtitle_fontsize", s.subtitle_fontsize))
                _write_ass(segments, ctx.stage_dir / "sub.ass",
                           font_name, fontsize, vw, vh)
                fontsdir = os.path.relpath(font_file.parent, ctx.stage_dir).replace(os.sep, "/")
                nxt = ctx.stage_dir / "step3_subtitled.mp4"
                run_ffmpeg(s, ["-i", cur.name,
                               "-vf", f"subtitles=sub.ass:fontsdir='{fontsdir}'",
                               "-c:a", "copy", nxt.name],
                           desc="字幕烧录", cwd=ctx.stage_dir)
                cur = nxt
            elif segments:
                logger.warning("assets/font 无可用字体,跳过字幕烧录")

        # 4. BGM 混音 bgm(默认 True)
        if pp.get("bgm", True):
            bgm = _pick_bgm(s, ctx)
            if bgm:
                volume = float(pp.get("bgm_volume", s.bgm_volume))
                nxt = ctx.stage_dir / "step4_bgm.mp4"
                run_ffmpeg(s, [
                    "-i", cur.name, "-stream_loop", "-1", "-i", str(bgm),
                    "-filter_complex",
                    f"[1:a]volume={volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
                    nxt.name,
                ], desc="BGM 混音", cwd=ctx.stage_dir)
                cur = nxt
            else:
                logger.warning("未找到 BGM,输出仅含原音")

        # 5. 视频水印 watermark_text(默认 None 不加;给了文字就加)
        watermark_text = pp.get("watermark_text")
        if watermark_text:
            try:
                nxt = ctx.stage_dir / "step5_watermark.mp4"
                add_text_watermark(
                    s, cur, nxt, ctx.stage_dir, str(watermark_text),
                    font_dir=s.assets / "font",
                    font_size=int(pp.get("watermark_fontsize", 60)),
                    font_color=pp.get("watermark_color", "white"),
                    position=pp.get("watermark_position", "top_right"),
                    margin=int(pp.get("watermark_margin", 40)),
                )
                cur = nxt
            except Exception as e:
                logger.warning(f"视频水印失败,保留无水印版本: {e}")

        # 6. 封面生成 cover(默认 True):抽中点帧,可选多色文字 + AI 标识
        outputs: dict[str, Path] = {}
        if pp.get("cover", True):
            try:
                outputs["cover"] = self._make_cover(ctx, cover_src, pp)
            except Exception as e:
                logger.warning(f"封面生成失败: {e}")

        final = ctx.stage_dir / "final.mp4"
        if cur.name != final.name:
            final.write_bytes(cur.read_bytes())
        outputs["final"] = final
        if (ctx.stage_dir / "sub.ass").exists():
            outputs["subtitle"] = ctx.stage_dir / "sub.ass"

        # 清理中间文件
        for f in ctx.stage_dir.glob("step*.mp4"):
            f.unlink()
        for f in ctx.stage_dir.glob("*.sc*n*.json"):
            f.unlink()
        (ctx.stage_dir / "asr_src.wav").unlink(missing_ok=True)
        return outputs

    def _subtitle_segments(self, ctx: StageContext, cur: Path, duration: float,
                           silence_applied: bool) -> list[dict]:
        """字幕分句 [{text, start, end}]: 文本来自原文(rewrite 产物、params.text、直接音频同名 .txt 或 ASR 转写)；
        tts/speech.wav 或 params.audio 存在时用 ASR 词级时间轴对齐，任何异常回退均分时长。"""
        text_file = ctx.job_dir / "rewrite" / "text.txt"
        text = (text_file.read_text(encoding="utf-8").strip()
                if text_file.exists() else (ctx.params.get("text") or "").strip())

        speech: Path | None = None
        if ctx.params.get("audio"):
            audio_path = Path(ctx.params["audio"])
            if not audio_path.is_absolute():
                audio_path = ctx.settings.abs_path(audio_path)
            if audio_path.exists():
                speech = audio_path
                if not text:
                    sidecar = audio_path.with_suffix(".txt")
                    if sidecar.exists():
                        text = sidecar.read_text(encoding="utf-8").strip()
                        logger.info(f"直接音频取同名字幕文本: {sidecar.name}")
        if speech is None:
            tts_speech = ctx.job_dir / "tts" / "speech.wav"
            if tts_speech.exists():
                speech = tts_speech

        # 若未提供文案但有驱动音频，尝试通过 ASR 自动转写字幕文本
        if not text and speech is not None and speech.exists():
            try:
                from ..providers import get_provider  # noqa: PLC0415
                asr_res = get_provider("asr", ctx.settings).transcribe(speech)
                text = (asr_res.get("text") or "").strip()
                if text:
                    logger.info(f"直接音频通过 ASR 自动生成字幕文案: {len(text)} 字")
            except Exception as e:
                logger.warning(f"直接音频 ASR 转写字幕失败: {e}")

        lines = split_subtitle_lines(text) if text else []
        if lines and speech is not None and speech.exists():
            try:
                from ..providers import get_provider  # noqa: PLC0415

                target = speech
                if silence_applied:
                    target = ctx.stage_dir / "asr_src.wav"
                    run_ffmpeg(ctx.settings, [
                        "-i", cur.name, "-vn", "-acodec", "pcm_s16le",
                        "-ar", "16000", "-ac", "1", target.name,
                    ], desc="ASR-提取音频", cwd=ctx.stage_dir)
                result = get_provider("asr", ctx.settings).transcribe(target)
                segments = _align_lines_to_asr(
                    lines, result.get("words") or [],
                    result.get("timestamp") or [], duration)
                if segments:
                    logger.info(f"字幕用 ASR 对齐时间轴: {len(segments)} 句")
                    return segments
            except Exception as e:
                logger.warning(f"ASR 对齐失败({e}),回退均分字幕")

        if not lines:
            return []
        per = duration / len(lines)
        return [{"text": line, "start": i * per, "end": (i + 1) * per}
                for i, line in enumerate(lines)]

    def _make_cover(self, ctx: StageContext, cover_src: Path, pp: dict) -> Path:
        """抽帧 → 可选多色文字(params["cover_text"]/["cover_highlights"])
        → AI 标识,产物 cover.png。"""
        s = ctx.settings
        raw = ctx.stage_dir / "cover_raw.png"
        extract_frame(s, cover_src, raw, frame_time=pp.get("cover_frame_time"))
        img = raw

        text = (ctx.params.get("cover_text") or "").strip()
        ai_text = pp.get("cover_ai_text", "此封面为AI生成")
        # 封面用 PIL 画,字体须覆盖文案+AI 标识全部字符(艺术字库可能缺字)
        font_file = pick_font_file(s.assets / "font", sample_text=text + ai_text)
        if text and font_file:
            first, second = _split_cover_text(text)
            highlights = ctx.params.get("cover_highlights") or []
            if isinstance(highlights, str):
                highlights = [w for w in re.split(r"[,，、\s]+", highlights) if w]
            out_text = ctx.stage_dir / "cover_text.png"
            add_multicolor_text(
                raw, out_text, first, second, list(highlights), font_file,
                font_size=int(pp.get("cover_fontsize", 90)),
                font_color=pp.get("cover_font_color", "#FFFFFF"),
                highlight_color=pp.get("cover_highlight_color", "#FFD600"),
                position=pp.get("cover_position", "bottom"),
            )
            img = out_text
        elif text:
            logger.warning("assets/font 无可用字体,封面不画文字")

        cover = ctx.stage_dir / "cover.png"
        add_ai_watermark(
            img, cover,
            text=ai_text,
            font_dir=s.assets / "font",
            font_size=int(pp.get("cover_ai_fontsize", 40)),
            position=pp.get("cover_ai_position", "top_right"),
        )
        raw.unlink(missing_ok=True)
        (ctx.stage_dir / "cover_text.png").unlink(missing_ok=True)
        return cover
