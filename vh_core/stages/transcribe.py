"""transcribe stage — ASR 转写(调 ASRProvider,默认 SenseVoice-Small)。

两个用途:① 克隆流程:转写 download 产物的原视频文案,供 rewrite 消费;
② 独立转写任意音频(params.transcribe_audio)。
产物:transcript.txt(纯文本)+ transcript.json(词级时间戳,供调试/字幕)。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..pipeline import Port, Stage, StageContext
from ..providers import get_provider


class TranscribeStage(Stage):
    name = "transcribe"
    title = "语音转写"
    category = "文本"
    description = "SenseVoice 识别音频/视频中的语音,输出文案与词级时间戳"
    inputs = {"audio": Port("audio", "音频/视频", required=True)}
    outputs = {"transcript": Port("text", "文案"), "transcript_json": Port("json", "带时间戳结果")}

    def _input_audio(self, ctx: StageContext) -> Path | None:
        if "audio" in ctx.inputs:
            return ctx.inputs["audio"]
        if ctx.params.get("transcribe_audio"):
            return ctx.settings.abs_path(Path(ctx.params["transcribe_audio"]))
        return ctx.input("audio", "download/source.mp4")

    def enabled(self, ctx: StageContext) -> bool:
        return self._input_audio(ctx) is not None

    def run(self, ctx: StageContext) -> dict[str, Path]:
        audio = self._input_audio(ctx)
        asr = get_provider("asr", ctx.settings)
        result = asr.transcribe(audio)

        txt = ctx.stage_dir / "transcript.txt"
        txt.write_text(result["text"], encoding="utf-8")
        js = ctx.stage_dir / "transcript.json"
        js.write_text(
            json.dumps(
                {
                    "text": result["text"],
                    "sentences": result["sentences"],
                    "words": result["words"],
                    "timestamp": result["timestamp"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {"transcript": txt, "transcript_json": js}
