"""rewrite stage — LLM 文案润色(OpenAI 兼容接口,依赖注入,去全局状态)。

未配置 llm_api_key 时**直通**(原文落盘)。
提示词:默认用内置合规版 DEFAULT_PROMPT(口播化润色 + 纠错,忠于原意,不做规避查重);
config llm_prompt_file 可指向自定义提示词文件(UTF-8 纯文本),由使用者对其内容负责。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ..config import Settings
from ..llm import chat, clean_api_key
from ..pipeline import Port, Stage, StageContext

# 内置默认提示词:只做口播化润色与纠错,保持原意与事实,不以"降重/规避查重"为目标
DEFAULT_PROMPT = """你是专业的短视频口播文案编辑。请在不改变原意和事实的前提下润色下面的文案:
1. 修正错别字、语病和语音转写错误(同音字、断句错误等);
2. 调整为适合口播的表达:句子简短、节奏清晰、口语自然;
3. 保持原文的观点、事实、数据和叙事顺序,不编造内容,不夸大效果;
4. 避免绝对化用语(如"最""第一""绝对"等)及违反广告法或平台规范的表述;
5. 字数与原文接近;
6. 只输出润色后的正文,不要任何解释、标注或问候。每行一个完整语义单元。
"""


def load_prompt(settings: Settings) -> str:
    """config llm_prompt_file 非空时读取自定义提示词,文件缺失直接报错(不静默回退)。"""
    if not settings.llm_prompt_file:
        return DEFAULT_PROMPT
    p = settings.abs_path(Path(settings.llm_prompt_file))
    if not p.is_file():
        raise FileNotFoundError(f"llm_prompt_file 指向的提示词文件不存在: {p}")
    prompt = p.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"提示词文件为空: {p}")
    return prompt


class RewriteStage(Stage):
    name = "rewrite"
    title = "文案润色"
    category = "文本"
    description = "LLM 口播化润色与纠错(未配置 LLM 时原样输出)"
    inputs = {"text": Port("text", "文案", required=True)}
    outputs = {"text": Port("text", "润色后文案")}

    def _source_text(self, ctx: StageContext) -> str:
        # 输入优先级:连线输入 > params.text(直给) > transcribe 产物(克隆流程)
        if "text" in ctx.inputs:
            return ctx.inputs["text"].read_text(encoding="utf-8").strip()
        text = (ctx.params.get("text") or "").strip()
        if text:
            return text
        transcript = ctx.input("text", "transcribe/transcript.txt")
        return transcript.read_text(encoding="utf-8").strip() if transcript else ""

    def enabled(self, ctx: StageContext) -> bool:
        """如果提供了直接驱动音频(对口型直通模式)，跳过文案改写阶段。"""
        if bool(ctx.params.get("audio")):
            return False
        return bool(self._source_text(ctx))

    def run(self, ctx: StageContext) -> dict[str, Path]:
        text = self._source_text(ctx)
        if not text:
            raise ValueError("rewrite 需要输入文案(params.text 或 transcribe 产物)")

        key = clean_api_key(ctx.settings.llm_api_key)
        if not key:
            logger.info("未配置 LLM api_key,rewrite 直通(原文落盘)")
            rewritten = text
        else:
            rewritten = self._llm_rewrite(ctx, text, key)

        out = ctx.stage_dir / "text.txt"
        out.write_text(rewritten, encoding="utf-8")
        return {"text": out}

    def _llm_rewrite(self, ctx: StageContext, text: str, key: str) -> str:
        s = ctx.settings
        prompt = load_prompt(s)  # 在 try 外:提示词配置错误应让 stage 失败,而不是回退原文
        try:
            result = chat(s, prompt, f"原文案:\n{text}")
        except Exception as e:
            # v1 行为:改写失败回退原文,不阻断流水线
            logger.warning(f"LLM 润色失败,回退原文: {e}")
            return text
        logger.info(f"LLM 润色完成({s.llm_model}),{len(text)}字 → {len(result)}字")
        return result
