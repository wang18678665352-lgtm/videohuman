"""providers — 重计算环节的本地实现接口(全部进程内推理)。

get_provider 按 engine 返回本地实现;路由优先级:显式 engine 参数 > config。
"""

from __future__ import annotations

from typing import Literal

from ..config import Settings

ProviderKind = Literal["asr", "tts", "avatar"]


def get_provider(kind: ProviderKind, settings: Settings, engine: str | None = None):
    """按 engine 返回 provider 实例。

    路由优先级:显式 engine 参数 > config(tts_engine/avatar_engine)。
    所有实现均为本地 GPU 进程内推理。
    """
    if kind == "tts":
        engine = engine or settings.tts_engine
        if engine == "cosyvoice3":
            from .local.tts_cosyvoice3 import CosyVoice3TTS

            return CosyVoice3TTS(settings)
        if engine in ("indextts2", "indextts", "index-tts"):
            from .local.tts_indextts2 import IndexTTS2TTS

            return IndexTTS2TTS(settings)
        raise ValueError(f"未知 TTS 引擎: {engine}(可选: cosyvoice3 / indextts2)")
    if kind == "avatar":
        engine = engine or settings.avatar_engine
        if engine == "lstmsync":
            from .local.avatar_lstmsync import LstmSyncAvatar

            return LstmSyncAvatar(settings)
        if engine != "musetalk":
            raise ValueError(f"未知数字人引擎: {engine}(可选: musetalk / lstmsync)")
        from .local.avatar_musetalk import MuseTalkAvatar

        return MuseTalkAvatar(settings)
    if kind == "asr":
        from .local.asr_sensevoice import SenseVoiceASR

        return SenseVoiceASR(settings)
    raise ValueError(f"未知 provider 类型: {kind}")
