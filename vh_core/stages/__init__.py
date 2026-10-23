"""stages — 流水线各环节(业务编排,不含重计算实现)。

全链路:download → transcribe → rewrite → tts → avatar → postprocess → publish。
download/transcribe/publish 按需启用(见各 Stage.enabled)。
"""

from .avatar import AvatarStage
from .download import DownloadStage
from .postprocess import PostprocessStage
from .publish import PublishStage
from .rewrite import RewriteStage
from .transcribe import TranscribeStage
from .tts import TTSStage

__all__ = [
    "AvatarStage",
    "DownloadStage",
    "PostprocessStage",
    "PublishStage",
    "RewriteStage",
    "TTSStage",
    "TranscribeStage",
]
