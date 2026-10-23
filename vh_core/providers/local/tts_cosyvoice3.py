"""TTSProvider 的 CosyVoice3 本地实现(进程内推理,lazy 单例)。

要点:
- sys.path 注入 third_party/CosyVoice 与 Matcha-TTS(推理必需);
- 模型目录为本地绝对路径,配合 HF_HUB_OFFLINE=1 完全离线;
- synthesize() 走 inference_zero_shot(零样本克隆),24kHz 输出。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from ...config import PROJECT_ROOT, Settings

_PATHS_INJECTED = False
_MODEL = None
_MODEL_SETTINGS_KEY: tuple | None = None


def _inject_paths(settings: Settings) -> None:
    global _PATHS_INJECTED
    if _PATHS_INJECTED:
        return
    for p in (settings.cosyvoice_repo, settings.cosyvoice_repo / "third_party" / "Matcha-TTS"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    _PATHS_INJECTED = True


def _load_model(settings: Settings):
    global _MODEL, _MODEL_SETTINGS_KEY
    model_path = settings.cosyvoice_model
    has_yaml = any((model_path / f"cosyvoice{v}.yaml").exists() for v in ("", "2", "3"))
    if not model_path.exists() or not has_yaml:
        fallback = PROJECT_ROOT.parent / "src_v2" / "models" / "Fun-CosyVoice3-0.5B-2512"
        if fallback.exists() and any((fallback / f"cosyvoice{v}.yaml").exists() for v in ("", "2", "3")):
            logger.info(f"主模型目录缺失，使用备用模型目录: {fallback}")
            model_path = fallback
        else:
            raise FileNotFoundError(
                f"CosyVoice 模型未找到或权重不完整: {model_path}\n"
                f"请运行 `uv run python download_weights.py ms` 下载模型权重，或在 configs/default.toml 中指定有效的 cosyvoice_model_dir。"
            )

    key = (str(model_path),)
    if _MODEL is not None and key == _MODEL_SETTINGS_KEY:
        return _MODEL
    _inject_paths(settings)
    logger.info(f"加载 CosyVoice3 模型: {model_path}")
    from cosyvoice.cli.cosyvoice import AutoModel

    _MODEL = AutoModel(model_dir=str(model_path))
    _MODEL_SETTINGS_KEY = key
    logger.info("CosyVoice3 模型加载完成")
    return _MODEL


class CosyVoice3TTS:
    """零样本克隆 TTS。prompt_text 需带 CV3 指令前缀。"""

    PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"

    def __init__(self, settings: Settings):
        self.settings = settings

    def synthesize(
        self,
        text: str,
        out_path: Path,
        ref_audio: Path | None = None,
        ref_text: str | None = None,
        speed: float = 1.0,
        **kwargs: Any,
    ) -> Path:
        import soundfile as sf
        import torch

        model = _load_model(self.settings)
        ref_audio = ref_audio or self.settings.default_ref_audio
        if not ref_audio.exists():
            raise FileNotFoundError(f"TTS 参考音频不存在: {ref_audio}")
        prompt_text = self.PROMPT_PREFIX + (ref_text or self.settings.tts_ref_text)

        chunks = []
        for out in model.inference_zero_shot(
            text,
            prompt_text,
            str(ref_audio),
            stream=False,
            speed=speed,
        ):
            chunks.append(out["tts_speech"])
        if not chunks:
            raise RuntimeError("CosyVoice3 未产出任何音频")
        speech = torch.cat(chunks, dim=1) if len(chunks) > 1 else chunks[0]

        out_path.parent.mkdir(parents=True, exist_ok=True)
        # torchaudio 2.9 的 save 依赖 torchcodec(Windows 不友好),直接用 soundfile
        sf.write(str(out_path), speech.squeeze(0).numpy(), model.sample_rate)
        logger.info(f"TTS 合成完成: {out_path} ({speech.shape[1] / model.sample_rate:.1f}s)")
        return out_path

    def unload(self) -> None:
        global _MODEL, _MODEL_SETTINGS_KEY
        _MODEL = None
        _MODEL_SETTINGS_KEY = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
