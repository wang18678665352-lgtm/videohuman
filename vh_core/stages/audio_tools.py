"""音频工具节点(纯 ffmpeg,无需 GPU):提取音频、配音对齐时长、替换视频音轨。"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ..ffmpeg_utils import probe_duration, run_ffmpeg
from ..pipeline import Param, Port, Stage, StageContext


class ExtractAudioStage(Stage):
    name = "extract_audio"
    title = "提取音频"
    category = "音频"
    description = "从视频中截取一段音频;常用于截取原声片段作为声音克隆的参考音色"
    inputs = {"media": Port("audio", "视频/音频", required=True)}
    outputs = {"audio": Port("audio", "音频")}
    params = (
        Param("start", "起始秒", "number", 0),
        Param("duration", "截取秒数", "number", 12, help="0 = 到结尾;作参考音色建议 8~15 秒"),
    )

    def run(self, ctx: StageContext) -> dict[str, Path]:
        src = ctx.inputs["media"]
        start = float(ctx.params.get("start") or 0)
        duration = float(ctx.params.get("duration") or 0)
        out = ctx.stage_dir / "audio.wav"
        args = ["-ss", f"{start:.3f}", "-i", str(src)]
        if duration > 0:
            args += ["-t", f"{duration:.3f}"]
        args += ["-vn", "-ac", "1", "-ar", "24000", "-acodec", "pcm_s16le", out.name]
        run_ffmpeg(ctx.settings, args, desc="提取音频", cwd=ctx.stage_dir)
        return {"audio": out}


def _atempo_chain(tempo: float) -> str:
    """atempo 单个滤镜只支持 0.5~2.0,超出时串联。"""
    parts = []
    while tempo > 2.0:
        parts.append("atempo=2.0")
        tempo /= 2.0
    while tempo < 0.5:
        parts.append("atempo=0.5")
        tempo /= 0.5
    parts.append(f"atempo={tempo:.5f}")
    return ",".join(parts)


class FitAudioStage(Stage):
    name = "fit_audio"
    title = "配音对齐时长"
    category = "音频"
    description = "把配音变速到与参考视频等长(不变调),不足部分补静音;用于翻译配音与原画面对齐"
    inputs = {"audio": Port("audio", "配音", required=True), "reference": Port("audio", "参考视频/音频", required=True)}
    outputs = {"audio": Port("audio", "对齐后配音")}
    params = (
        Param("min_tempo", "最慢倍速", "number", 0.8, help="低于此倍速不再放慢,改为末尾补静音"),
        Param("max_tempo", "最快倍速", "number", 1.25, help="超过此倍速不再加快,成片会比原视频长"),
    )

    def run(self, ctx: StageContext) -> dict[str, Path]:
        s = ctx.settings
        audio, ref = ctx.inputs["audio"], ctx.inputs["reference"]
        a_dur, r_dur = probe_duration(s, audio), probe_duration(s, ref)
        lo = float(ctx.params.get("min_tempo") or 0.8)
        hi = float(ctx.params.get("max_tempo") or 1.25)
        want = a_dur / r_dur if r_dur > 0 else 1.0
        tempo = min(max(want, lo), hi)
        if abs(tempo - want) > 1e-3:
            logger.warning(f"配音需 {want:.2f} 倍速才能与参考等长,已限制为 {tempo:.2f}(范围 {lo}~{hi})")
        # 变速后不足参考时长的部分补静音,超出的保留(不截断语音)
        flt = f"{_atempo_chain(tempo)},apad=whole_dur={r_dur:.3f}"
        out = ctx.stage_dir / "audio.wav"
        run_ffmpeg(
            s, ["-i", str(audio), "-af", flt, "-ac", "1", "-ar", "24000", out.name], desc="配音对齐", cwd=ctx.stage_dir
        )
        logger.info(f"配音 {a_dur:.1f}s → 目标 {r_dur:.1f}s,倍速 {tempo:.3f}")
        return {"audio": out}


class MuxAudioStage(Stage):
    name = "mux_audio"
    title = "替换音轨"
    category = "视频"
    description = "用新配音替换视频原声(可保留少量原声作环境音),画面不变"
    inputs = {"video": Port("video", "视频", required=True), "audio": Port("audio", "新音轨", required=True)}
    outputs = {"video": Port("video", "视频")}
    params = (Param("original_volume", "原声保留音量", "number", 0, help="0 = 完全替换;0.1~0.2 可保留环境音"),)

    def run(self, ctx: StageContext) -> dict[str, Path]:
        video, audio = ctx.inputs["video"], ctx.inputs["audio"]
        keep = float(ctx.params.get("original_volume") or 0)
        out = ctx.stage_dir / "video.mp4"
        args = ["-i", str(video), "-i", str(audio)]
        if keep > 0:
            args += [
                "-filter_complex",
                f"[0:a]volume={keep}[bg];[bg][1:a]amix=inputs=2:duration=longest:normalize=0[a]",
                "-map",
                "0:v",
                "-map",
                "[a]",
            ]
        else:
            args += ["-map", "0:v", "-map", "1:a"]
        # 画面直拷不重编码;时长取较长的流(配音比画面长时,末尾画面由播放器停在最后一帧)
        args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out.name]
        run_ffmpeg(ctx.settings, args, desc="替换音轨", cwd=ctx.stage_dir)
        return {"video": out}
