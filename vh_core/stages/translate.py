"""translate 节点 — LLM 把文案翻译成目标语言,供配音使用。

与 rewrite 不同:翻译失败不回退原文(原文配进目标语言的视频没有意义),直接让节点失败。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ..llm import chat
from ..pipeline import Param, Port, Stage, StageContext

LANGUAGES = (
    "英语",
    "日语",
    "韩语",
    "西班牙语",
    "法语",
    "德语",
    "葡萄牙语",
    "俄语",
    "阿拉伯语",
    "泰语",
    "越南语",
    "印尼语",
    "中文",
)

PROMPT = """你是专业的视频本地化译者。把用户给出的口播文案翻译成{lang},译文将直接用于配音:
1. 意思准确,口语自然,符合{lang}母语者的表达习惯;
2. 保持原文的分行:原文一行对应译文一行,不合并、不拆分;
3. 译文朗读时长尽量与原文接近,避免过度扩写;
4. 人名、品牌名等专有名词保留或使用通行译名;
5. 只输出译文,不要任何解释、注音或原文。
{extra}"""


class TranslateStage(Stage):
    name = "translate"
    title = "文案翻译"
    category = "文本"
    description = "LLM 把文案翻译成目标语言(需配置 LLM),逐行对应便于配音与字幕"
    inputs = {"text": Port("text", "原文", required=True)}
    outputs = {"text": Port("text", "译文")}
    params = (
        Param("target_lang", "目标语言", "select", "英语", LANGUAGES),
        Param("instructions", "补充要求", "text", help="如:面向美国年轻人,语气轻松"),
    )

    def run(self, ctx: StageContext) -> dict[str, Path]:
        text = ctx.inputs["text"].read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("待翻译文案为空")
        lang = (ctx.params.get("target_lang") or "英语").strip()
        extra = (ctx.params.get("instructions") or "").strip()
        result = chat(ctx.settings, PROMPT.format(lang=lang, extra=f"补充要求:{extra}" if extra else ""), text, 0.3)
        logger.info(f"翻译完成 → {lang}:{len(text)} → {len(result)} 字符")
        out = ctx.stage_dir / "text.txt"
        out.write_text(result, encoding="utf-8")
        return {"text": out}
