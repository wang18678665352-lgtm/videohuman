"""输入节点:把任务参数(文案/素材文件)变成工作流里的产物,供下游连线使用。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..pipeline import Param, Port, Stage, StageContext


def link_or_copy(src: Path, dst: Path) -> Path:
    """素材进入 job 目录:优先硬链接(不占额外空间),跨盘等失败时复制。"""
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)
    return dst


class _InputFile(Stage):
    kind = ""
    category = "输入"

    def run(self, ctx: StageContext) -> dict[str, Path]:
        raw = (ctx.params.get("path") or "").strip()
        if not raw:
            raise ValueError(f"节点需要选择{self.title}文件(参数 path)")
        src = ctx.settings.abs_path(Path(raw))
        if not src.is_file():
            raise FileNotFoundError(f"素材文件不存在: {src}")
        out = link_or_copy(src, ctx.stage_dir / f"{self.kind}{src.suffix.lower()}")
        return {self.kind: out}


class InputTextStage(Stage):
    name = "input_text"
    title = "文案输入"
    category = "输入"
    description = "直接填写口播文案"
    outputs = {"text": Port("text", "文案")}
    params = (Param("text", "文案", "text", required=True),)

    def run(self, ctx: StageContext) -> dict[str, Path]:
        text = (ctx.params.get("text") or "").strip()
        if not text:
            raise ValueError("文案输入为空")
        out = ctx.stage_dir / "text.txt"
        out.write_text(text, encoding="utf-8")
        return {"text": out}


class InputAudioStage(_InputFile):
    name = "input_audio"
    title = "音频输入"
    kind = "audio"
    description = "选择素材库里的音频(录音、参考音色等)"
    outputs = {"audio": Port("audio", "音频")}
    params = (Param("path", "音频文件", "asset_audio", required=True),)


class InputVideoStage(_InputFile):
    name = "input_video"
    title = "视频输入"
    kind = "video"
    description = "选择素材库里的视频(形象视频、待翻译视频等)"
    outputs = {"video": Port("video", "视频")}
    params = (Param("path", "视频文件", "asset_video", required=True),)
