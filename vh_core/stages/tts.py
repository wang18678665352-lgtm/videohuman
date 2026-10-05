"""tts stage — 语音合成,调 TTSProvider(默认 CosyVoice3)。

输入优先级:rewrite 产物 text.txt > params.text。
参数:speed / ref_audio / ref_text 等经 params["tts"] 透传给 provider。
参考音文本未给时自动解析:同名 .txt sidecar → 默认参考音用 config 默认文本
→ 其余用 ASR 识别(见 _resolve_ref_text)。
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

from ..pipeline import Stage, StageContext
from ..providers import get_provider

# 参考音文本只保留中英文/数字/常用标点,滤掉 SenseVoice 情感标记等残留(emoji 等)
_REF_TEXT_DROP = re.compile(
    "[^\\w\\s,.!?;:—·…-、。"
    "\\uff0c\\uff01\\uff1f\\uff1a\\uff1b\\u3010\\u3011\\u300a\\u300b\\uff08\\uff09]+"
)


def _clean_ref_text(text: str) -> str:
    return _REF_TEXT_DROP.sub("", text).strip()


class TTSStage(Stage):
    name = "tts"

    def enabled(self, ctx: StageContext) -> bool:
        """如果提供了直接驱动音频(对口型直通模式)，跳过 TTS 阶段。"""
        if bool(ctx.params.get("audio")):
            return False
        return True

    def _resolve_ref_text(self, ctx: StageContext, ref_audio: Path | None) -> str:
        """参考音文本:params 直给 > 同名 .txt sidecar > 默认参考音用 config 默认文本
        > 其余情况用 ASR 自动识别(结果存 stage_dir/ref_text.txt 备查)。"""
        explicit = (ctx.params.get("ref_text") or "").strip()
        if explicit:
            return explicit
        if ref_audio is not None:
            sidecar = ref_audio.with_suffix(".txt")
            if sidecar.exists():
                text = sidecar.read_text(encoding="utf-8").strip()
                logger.info(f"参考音文本取同名文件: {sidecar.name} ({len(text)} 字)")
                return text
            if ref_audio.resolve() != ctx.settings.default_ref_audio.resolve():
                text = _clean_ref_text(
                    get_provider("asr", ctx.settings).transcribe(ref_audio).get("text") or ""
                )
                if text:
                    (ctx.stage_dir / "ref_text.txt").write_text(text, encoding="utf-8")
                    logger.info(f"参考音文本 ASR 自动识别: {len(text)} 字")
                return text
        # 默认参考音:返回空串,provider 会用 config tts_ref_text
        return ""

    def run(self, ctx: StageContext) -> dict[str, Path]:
        text_file = ctx.job_dir / "rewrite" / "text.txt"
        if text_file.exists():
            text = text_file.read_text(encoding="utf-8").strip()
        else:
            text = (ctx.params.get("text") or "").strip()
        if not text:
            raise ValueError("tts 没有可用文本(rewrite 产物与 params.text 均为空)")

        tts_params = dict(ctx.params.get("tts", {}))
        engine = ctx.params.get("tts_engine")
        ref_audio: Path | None = None
        if ctx.params.get("ref_audio"):
            ref_audio = ctx.settings.abs_path(Path(ctx.params["ref_audio"]))
            tts_params["ref_audio"] = ref_audio
        ref_text = self._resolve_ref_text(ctx, ref_audio)
        if ref_text:
            tts_params["ref_text"] = ref_text

        out = ctx.stage_dir / "speech.wav"
        provider = get_provider("tts", ctx.settings, engine=engine)
        provider.synthesize(text, out, **tts_params)
        logger.info(f"TTS 文本长度 {len(text)} 字")
        return {"speech": out}
