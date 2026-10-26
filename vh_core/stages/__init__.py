"""stages — 流水线/工作流的各个步骤(业务编排,不含重计算实现)。

经典数字人流水线:download → transcribe → rewrite → tts → avatar → postprocess → publish。
工作流可自由组合 STEP_TYPES 里的全部步骤(见 vh_core.workflow)。
"""

from .audio_tools import ExtractAudioStage, FitAudioStage, MuxAudioStage
from .avatar import AvatarStage
from .broll import BrollStage
from .download import DownloadStage
from .inputs import InputAudioStage, InputTextStage, InputVideoStage
from .postprocess import PostprocessStage
from .publish import PublishStage
from .rewrite import RewriteStage
from .transcribe import TranscribeStage
from .translate import TranslateStage
from .tts import TTSStage

# 工作流可用的步骤类型(顺序即编辑器节点面板的展示顺序)
STEP_TYPES = {
    cls.name: cls
    for cls in (
        InputTextStage,
        InputVideoStage,
        InputAudioStage,
        DownloadStage,
        TranscribeStage,
        RewriteStage,
        TranslateStage,
        TTSStage,
        ExtractAudioStage,
        FitAudioStage,
        AvatarStage,
        BrollStage,
        MuxAudioStage,
        PostprocessStage,
        PublishStage,
    )
}

__all__ = [
    "STEP_TYPES",
    "AvatarStage",
    "BrollStage",
    "DownloadStage",
    "ExtractAudioStage",
    "FitAudioStage",
    "InputAudioStage",
    "InputTextStage",
    "InputVideoStage",
    "MuxAudioStage",
    "PostprocessStage",
    "PublishStage",
    "RewriteStage",
    "TTSStage",
    "TranscribeStage",
    "TranslateStage",
]
