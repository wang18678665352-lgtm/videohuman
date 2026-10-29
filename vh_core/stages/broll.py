"""broll 节点 — 素材混剪画面:按配音的句子节奏切镜头,轮换素材库里的图片/视频,不需要出镜人物。

1. 分句:文案按句末标点切句(无文案时整段按时长均匀切);
2. 时间轴:ASR 词级时间戳对齐每句起点(失败或关闭时按字数比例估算);
3. 镜头规划:句子起点作切点,短于 min_shot 的合并、长于 max_shot 的均分;
4. 渲染:图片缓慢推近(可关),视频裁成画布比例并循环补足时长,逐镜头编码后无损拼接,
   最后混入配音。纯 ffmpeg 完成,GPU 只在 ASR 对齐时用到(可关)。
"""

from __future__ import annotations

import itertools
import math
import random
import re
from pathlib import Path

from loguru import logger

from ..ffmpeg_utils import probe_duration, run_ffmpeg
from ..pipeline import Param, Port, Stage, StageContext
from .postprocess import IMAGE_EXTS, _align_lines_to_asr, _norm_align

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

_SENTENCE_END = re.compile(r"(?<=[。!?！？;；\n])|(?<=[.?!;])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s and s.strip()]


def sentence_starts_by_length(lines: list[str], total: float) -> list[float]:
    """无 ASR 时按(去空白)字数比例估算每句起点。"""
    lens = [max(len(_norm_align(line)), 1) for line in lines]
    acc, starts = 0, []
    for n in lens:
        starts.append(total * acc / sum(lens))
        acc += n
    return starts


def plan_shots(cut_points: list[float], total: float, min_shot: float, max_shot: float) -> list[float]:
    """由切点规划镜头时长列表(总和 = total)。"""
    max_shot = max(max_shot, min_shot)
    bounds = [0.0, *sorted(c for c in set(cut_points) if 0 < c < total), total]
    merged: list[float] = []
    for a, b in itertools.pairwise(bounds):
        d = b - a
        if merged and merged[-1] < min_shot:
            merged[-1] += d
        else:
            merged.append(d)
    if len(merged) > 1 and merged[-1] < min_shot:
        merged[-2] += merged.pop()
    shots: list[float] = []
    for d in merged:
        n = max(1, math.ceil(d / max_shot - 1e-9))
        shots.extend([d / n] * n)
    return shots


def list_media(media_dir: Path) -> list[Path]:
    if not media_dir.is_dir():
        raise FileNotFoundError(f"素材目录不存在: {media_dir}")
    files = [p for p in sorted(media_dir.iterdir()) if p.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS]
    if not files:
        raise FileNotFoundError(f"素材目录里没有图片或视频: {media_dir}")
    return files


class BrollStage(Stage):
    name = "broll"
    title = "素材混剪画面"
    category = "视频"
    description = "按配音的句子节奏轮换素材库的图片/视频生成画面,无需出镜人物"
    inputs = {"audio": Port("audio", "配音", required=True), "text": Port("text", "文案(按句切镜头)")}
    outputs = {"video": Port("video", "混剪视频")}
    params = (
        Param(
            "media_dir", "素材目录", "asset_dir", "", help="留空 = 素材库下的 broll/ 目录;图片(png/jpg/webp)与视频混放"
        ),
        Param("order", "素材顺序", "select", "sequential", ("sequential", "random")),
        Param("width", "画布宽", "integer", 1080),
        Param("height", "画布高", "integer", 1920),
        Param("fps", "帧率", "integer", 30),
        Param("min_shot", "最短镜头(秒)", "number", 2.0),
        Param("max_shot", "最长镜头(秒)", "number", 5.0),
        Param("image_motion", "图片缓慢推近", "bool", True),
        Param("align_with_asr", "用 ASR 对齐切点", "bool", True, help="关闭则按字数估算,不占 GPU"),
    )

    def _cut_points(self, ctx: StageContext, audio: Path, total: float) -> list[float]:
        text_file = ctx.inputs.get("text")
        lines = split_sentences(text_file.read_text(encoding="utf-8")) if text_file else []
        if not lines:
            return []
        if ctx.params.get("align_with_asr", True):
            try:
                from ..providers import get_provider

                res = get_provider("asr", ctx.settings).transcribe(audio)
                segs = _align_lines_to_asr(lines, res.get("words") or [], res.get("timestamp") or [], total)
                if segs:
                    logger.info(f"镜头切点用 ASR 对齐: {len(segs)} 句")
                    return [seg["start"] for seg in segs]
            except Exception as e:
                logger.warning(f"ASR 对齐失败({e}),按字数估算切点")
        return sentence_starts_by_length(lines, total)

    def _render_shot(self, ctx: StageContext, src: Path, frames: int, out: Path, w: int, h: int, fps: int) -> None:
        cover = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an"]
        if src.suffix.lower() in IMAGE_EXTS:
            if ctx.params.get("image_motion", True):
                # 先放大一倍再 zoompan,推近更平滑(直接在目标分辨率上 zoompan 会抖)
                vf = (
                    f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
                    f"zoompan=z='min(1+0.12*on/{frames},1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                    f":d=1:s={w}x{h}:fps={fps},setsar=1"
                )
            else:
                vf = f"{cover},fps={fps}"
            args = ["-framerate", str(fps), "-loop", "1", "-i", str(src), "-vf", vf, "-frames:v", str(frames)]
        else:
            args = ["-stream_loop", "-1", "-i", str(src), "-vf", f"{cover},fps={fps}", "-frames:v", str(frames)]
        run_ffmpeg(ctx.settings, [*args, *enc, out.name], desc=f"镜头 {out.stem}", cwd=ctx.stage_dir)

    def run(self, ctx: StageContext) -> dict[str, Path]:
        s, p = ctx.settings, ctx.params
        audio = ctx.inputs["audio"]
        total = probe_duration(s, audio)
        media_dir = p.get("media_dir")
        media = list_media(s.abs_path(Path(media_dir)) if media_dir else s.assets / "broll")
        if p.get("order") == "random":
            random.Random(ctx.job_id).shuffle(media)  # 以 job_id 为种子:重跑结果稳定
        w, h, fps = int(p.get("width") or 1080), int(p.get("height") or 1920), int(p.get("fps") or 30)
        shots = plan_shots(
            self._cut_points(ctx, audio, total),
            total,
            float(p.get("min_shot") or 2.0),
            float(p.get("max_shot") or 5.0),
        )
        # 镜头时长换算成帧数(累计取整,总帧数 = 配音时长向上取整),拼接后画面与配音误差不超过 1 帧,
        # 无需再截断(-shortest / -t 配合流拷贝都截不准)
        total_frames = math.ceil(total * fps)
        cuts = [0]
        acc = 0.0
        for d in shots[:-1]:
            acc += d
            cuts.append(min(round(acc * fps), total_frames - 1))
        cuts.append(total_frames)
        frame_counts = [b - a for a, b in itertools.pairwise(cuts) if b > a]
        logger.info(f"混剪 {len(shots)} 个镜头,素材 {len(media)} 个,总时长 {total:.1f}s")

        parts: list[Path] = []
        for i, frames in enumerate(frame_counts):
            part = ctx.stage_dir / f"shot_{i:03d}.mp4"
            self._render_shot(ctx, media[i % len(media)], frames, part, w, h, fps)
            parts.append(part)
        concat = ctx.stage_dir / "shots.txt"
        concat.write_text("".join(f"file '{x.name}'\n" for x in parts), encoding="utf-8")
        out = ctx.stage_dir / "video.mp4"
        run_ffmpeg(
            s,
            ["-f", "concat", "-safe", "0", "-i", concat.name, "-i", str(audio),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out.name],
            desc="拼接镜头并混入配音",
            cwd=ctx.stage_dir,
        )  # fmt: skip
        for x in parts:
            x.unlink(missing_ok=True)
        concat.unlink(missing_ok=True)
        return {"video": out}
