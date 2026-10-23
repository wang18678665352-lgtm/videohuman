"""ASRProvider 的 SenseVoice-Small 本地实现(funasr,进程内推理,lazy 单例)。

- 本地模型目录 + disable_update=True,纯离线;
- output_timestamp=True 产出词级时间戳(CTC 强制对齐,无需 VAD);
- 返回 dict(text/words/timestamp/sentences),sentences 按标点分句供字幕使用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from ...config import Settings

_MODEL = None

_PUNCT = "，。！？；,.!?;"

# soundfile/torchaudio 能直接读的音频格式;其余(如 mp4)先抽成 16k wav
_DIRECT_AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}


def _load_model(settings: Settings):
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    from funasr import AutoModel

    model_dir = settings.models / "SenseVoiceSmall"
    if not model_dir.is_dir():
        raise FileNotFoundError(f"SenseVoice 模型目录不存在: {model_dir}")
    logger.info(f"加载 SenseVoice-Small: {model_dir}")
    _MODEL = AutoModel(
        model=str(model_dir),
        disable_update=True,  # 跳过 PyPI 版本检查,纯离线
        device="cuda:0",
    )
    logger.info("SenseVoice-Small 加载完成")
    return _MODEL


def _group_sentences(text: str, words: list[str], ts: list[list[float]]) -> list[dict]:
    """按标点把词级序列分句,返回 [{text, start, end}](秒)。

    SenseVoice 的 timestamp 单位是毫秒,这里统一转秒。
    """
    sentences: list[dict] = []
    cur_words: list[str] = []
    cur_start: float | None = None
    pos = 0  # 当前在 text 中消费到的位置(words 不含标点,text 含)
    for w, (ws, we) in zip(words, ts):
        if cur_start is None:
            cur_start = ws
        cur_words.append(w)
        pos += len(w)
        # text 中紧跟本词之后的字符若是标点,则收句
        trail = text[pos : pos + 1] if pos < len(text) else ""
        if trail and trail in _PUNCT:
            sentences.append(
                {
                    "text": "".join(cur_words) + trail,
                    "start": round(cur_start / 1000, 2),
                    "end": round(we / 1000, 2),
                }
            )
            cur_words = []
            cur_start = None
            pos += 1
    if cur_words:
        sentences.append(
            {
                "text": "".join(cur_words),
                "start": round((cur_start or 0.0) / 1000, 2),
                "end": round((ts[-1][1] if ts else 0.0) / 1000, 2),
            }
        )
    # 滤掉只剩标点的碎片句(词表与 ITN 文本对不齐时的退化产物)
    return [s for s in sentences if s["text"].strip(_PUNCT)]


class SenseVoiceASR:
    def __init__(self, settings: Settings):
        self.settings = settings

    def transcribe(self, audio: Path, language: str = "zh", **kwargs: Any) -> dict:
        from funasr.utils.postprocess_utils import (
            rich_transcription_postprocess,
        )

        model = _load_model(self.settings)
        audio = Path(audio)
        if not audio.exists():
            raise FileNotFoundError(f"ASR 输入音频不存在: {audio}")
        if audio.suffix.lower() not in _DIRECT_AUDIO_EXTS:
            # funasr 对 mp4 等非音频容器会回退到 subprocess 调 PATH 里的 ffmpeg
            # (本机 PATH 没有 ffmpeg 时 WinError 2);先用项目自己的 ffmpeg 解析链抽轨
            from ...ffmpeg_utils import run_ffmpeg

            wav = audio.with_name(f"{audio.stem}.asr16k.wav")
            run_ffmpeg(
                self.settings, ["-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", str(wav)], desc="ASR 抽轨 16k wav"
            )
            audio = wav

        res = model.generate(
            input=str(audio),
            language=language,
            use_itn=True,
            output_timestamp=True,
            batch_size_s=60,
            **kwargs,
        )
        item = res[0]
        text = rich_transcription_postprocess(item["text"])
        words = item.get("words") or []
        ts = item.get("timestamp") or []
        sentences = _group_sentences(text, words, ts) if words and ts else []
        logger.info(f"ASR 转写完成: {len(text)} 字,{len(sentences)} 句")
        return {
            "text": text,
            "words": words,
            "timestamp": ts,
            "sentences": sentences,
        }

    def unload(self) -> None:
        global _MODEL
        _MODEL = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
