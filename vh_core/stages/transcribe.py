"""transcribe stage — ASR 转写(调 ASRProvider,默认 SenseVoice-Small)。

两个用途:① 克隆流程:转写 download 产物的原视频文案,供 rewrite 消费;
② 独立转写任意音频(params.transcribe_audio)。
产物:transcript.txt(纯文本)+ transcript.json(词级时间戳,供调试/字幕)。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..pipeline import Stage, StageContext
from ..providers import get_provider


class TranscribeStage(Stage):
    name = "transcribe"

    def _input_audio(self, ctx: StageContext) -> Path | None:
        if ctx.params.get("transcribe_audio"):
            return Path(ctx.params["transcribe_audio"])
        src = ctx.job_dir / "download" / "source.mp4"
        return src if src.exists() else None

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
