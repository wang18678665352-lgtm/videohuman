"""TTSProvider 的 IndexTTS-2 本地实现(进程内推理,lazy 单例)。

要点:
- sys.path 注入 third_party/index-tts;
- 模型目录为本地绝对路径,加载 IndexTTS2 推理类;
- synthesize() 调用 infer(spk_audio_prompt, text, output_path)。
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
    repo_path = str(settings.indextts_repo)
    if repo_path not in sys.path:
        sys.path.insert(0, repo_path)
    _PATHS_INJECTED = True


def _load_model(settings: Settings):
    global _MODEL, _MODEL_SETTINGS_KEY
    model_path = settings.indextts_model
    if not model_path.exists() or not (model_path / "config.yaml").exists():
        fallback = PROJECT_ROOT.parent / "src_v2" / "models" / "IndexTTS-2"
        if fallback.exists() and (fallback / "config.yaml").exists():
            logger.info(f"主模型目录缺失，使用备用 IndexTTS-2 模型目录: {fallback}")
            model_path = fallback
        else:
            raise FileNotFoundError(
                f"IndexTTS-2 模型未找到或权重不完整: {model_path}\n"
                f"请确保 models/IndexTTS-2 包含 config.yaml, gpt.pth, s2mel.pth 等权重文件。"
            )

    key = (str(model_path),)
    if _MODEL is not None and _MODEL_SETTINGS_KEY == key:
        return _MODEL

    _inject_paths(settings)
    logger.info(f"加载 IndexTTS-2 模型: {model_path}")
    import torch
    from indextts.infer_v2 import IndexTTS2  # noqa: PLC0415

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    cfg_path = str(model_path / "config.yaml")

    _MODEL = IndexTTS2(
        cfg_path=cfg_path,
        model_dir=str(model_path),
        use_fp16=torch.cuda.is_available(),
        device=device,
        use_cuda_kernel=False,
    )
    _MODEL_SETTINGS_KEY = key
    logger.info("IndexTTS-2 模型加载完成")
    return _MODEL


class IndexTTS2TTS:
    """IndexTTS-2 零样本情感克隆 TTS。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def synthesize(
        self,
        text: str,
        out_path: Path,
        ref_audio: Path | None = None,
        **kwargs: Any,
    ) -> Path:
        model = _load_model(self.settings)
        ref_audio = ref_audio or self.settings.default_ref_audio
        if not ref_audio.exists():
            raise FileNotFoundError(f"TTS 参考音频不存在: {ref_audio}")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        # 支持传递 emotion 或其他 generation 参数
        infer_kwargs: dict[str, Any] = {}
        for k in ("emo_audio_prompt", "emo_alpha", "emo_vector", "use_emo_text", "emo_text", "interval_silence"):
            if k in kwargs and kwargs[k] is not None:
                infer_kwargs[k] = kwargs[k]

        logger.info(f"IndexTTS-2 开始合成语音: text='{text[:30]}...' ref='{ref_audio}'")
        model.infer(
            spk_audio_prompt=str(ref_audio),
            text=text,
            output_path=str(out_path),
            **infer_kwargs,
        )

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(f"IndexTTS-2 生成音频失败，输出文件不存在或为空: {out_path}")

        logger.info(f"IndexTTS-2 合成完成: {out_path} ({out_path.stat().st_size} bytes)")
        return out_path

    def unload(self) -> None:
        global _MODEL, _MODEL_SETTINGS_KEY
        _MODEL = None
        _MODEL_SETTINGS_KEY = None
        try:
            import torch  # noqa: PLC0415

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
