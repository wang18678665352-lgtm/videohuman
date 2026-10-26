"""avatar stage — 数字人生成,调 AvatarProvider(默认 MuseTalk,备选 LstmSync)。

音频取 tts 产物 speech.wav;素材视频来自 params.avatar_video(或 config 默认)。
params["avatar"] 透传 provider 级参数(batch_size/fps/extra_margin/use_float16 等)。
"""

from __future__ import annotations

from pathlib import Path

from ..pipeline import Param, Port, Stage, StageContext
from ..providers import get_provider


class AvatarStage(Stage):
    name = "avatar"
    title = "数字人口型同步"
    category = "视频"
    description = "用驱动音频让形象视频里的人物对口型(MuseTalk / LstmSync)"
    inputs = {"audio": Port("audio", "驱动音频", required=True), "video": Port("video", "形象视频")}
    outputs = {"avatar": Port("video", "口播视频")}
    params = (
        Param("avatar_engine", "数字人引擎", "select", "musetalk", ("musetalk", "lstmsync")),
        Param("avatar_video", "形象视频", "asset_video", help="未连线形象视频时使用"),
        Param("avatar.batch_size", "推理批大小", "integer", 8),
    )

    def _resolve_audio(self, ctx: StageContext) -> Path:
        """驱动音频解析: 连线输入 > params.audio(直接对口型) > tts/speech.wav。"""
        if "audio" in ctx.inputs:
            return ctx.inputs["audio"]
        if ctx.params.get("audio"):
            audio = Path(ctx.params["audio"])
            if not audio.is_absolute():
                audio = ctx.settings.abs_path(audio)
            if not audio.exists():
                raise FileNotFoundError(f"指定的驱动音频不存在: {audio}")
            return audio
        tts_audio = ctx.input("audio", "tts/speech.wav")
        if tts_audio is not None:
            return tts_audio
        raise FileNotFoundError("avatar 需要驱动音频: 请在参数中提供 audio (已有语音) 或先运行 tts 阶段")

    def run(self, ctx: StageContext) -> dict[str, Path]:
        audio = self._resolve_audio(ctx)

        engine = ctx.params.get("avatar_engine")
        video = ctx.inputs.get("video") or ctx.params.get("avatar_video") or ctx.settings.avatar_video
        if not video:
            raise ValueError("avatar 需要素材视频:params.avatar_video 或 config avatar_video")
        video = Path(video)
        if not video.is_absolute():
            video = ctx.settings.abs_path(video)

        avatar_params = dict(ctx.params.get("avatar", {}))
        out = ctx.stage_dir / "avatar.mp4"
        provider = get_provider("avatar", ctx.settings, engine=engine)
        provider.generate(
            video=video,
            audio=audio,
            out_path=out,
            work_dir=ctx.stage_dir / "tmp",
            **avatar_params,
        )
        return {"avatar": out}
