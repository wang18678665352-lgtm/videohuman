"""rewrite stage — LLM 文案润色(OpenAI 兼容接口,依赖注入,去全局状态)。

未配置 llm_api_key 时**直通**(原文落盘)。
提示词:默认用内置合规版 DEFAULT_PROMPT(口播化润色 + 纠错,忠于原意,不做规避查重);
config llm_prompt_file 可指向自定义提示词文件(UTF-8 纯文本),由使用者对其内容负责。
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from ..config import Settings
from ..pipeline import Stage, StageContext

# 内置默认提示词:只做口播化润色与纠错,保持原意与事实,不以"降重/规避查重"为目标
DEFAULT_PROMPT = """你是专业的短视频口播文案编辑。请在不改变原意和事实的前提下润色下面的文案:
1. 修正错别字、语病和语音转写错误(同音字、断句错误等);
2. 调整为适合口播的表达:句子简短、节奏清晰、口语自然;
3. 保持原文的观点、事实、数据和叙事顺序,不编造内容,不夸大效果;
4. 避免绝对化用语(如"最""第一""绝对"等)及违反广告法或平台规范的表述;
5. 字数与原文接近;
6. 只输出润色后的正文,不要任何解释、标注或问候。每行一个完整语义单元。
"""


def clean_api_key(api_key_input: str | None) -> str | None:
    """清理 API Key 格式,兼容 v1 的 JSON 列表格式输入。"""
    if not api_key_input:
        return None
    key = api_key_input.strip()
    if key.startswith("[") and key.endswith("]"):
        try:
            key_list = json.loads(key)
            if isinstance(key_list, list) and key_list:
                key = key_list[0]
        except (json.JSONDecodeError, TypeError):
            key = key.strip("[]\"' ")
    return key or None


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

    def enabled(self, ctx: StageContext) -> bool:
        """如果提供了直接驱动音频(对口型直通模式)，跳过文案改写阶段。"""
        if bool(ctx.params.get("audio")):
            return False
        text = (ctx.params.get("text") or "").strip()
        transcript = ctx.job_dir / "transcribe" / "transcript.txt"
        return bool(text) or transcript.exists()

    def run(self, ctx: StageContext) -> dict[str, Path]:
        # 输入优先级:params.text(直给) > transcribe 产物(克隆流程)
        text = (ctx.params.get("text") or "").strip()
        if not text:
            transcript = ctx.job_dir / "transcribe" / "transcript.txt"
            if transcript.exists():
                text = transcript.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("rewrite 需要 params.text 或 transcribe 产物 transcript.txt")

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
        from openai import OpenAI

        s = ctx.settings
        prompt = load_prompt(s)  # 在 try 外:提示词配置错误应让 stage 失败,而不是回退原文
        # config.toml/env 里常带着引号书写,统一剥掉;不设 base_url 时 SDK 默认走官方地址
        base_url = s.llm_base_url.strip().strip("\"'") or None
        # 显式超时 + 有限重试:LLM 接口抖动不应让整条流水线卡死在这里
        client = OpenAI(api_key=key, base_url=base_url, timeout=s.llm_timeout, max_retries=2)
        try:
            completion = client.chat.completions.create(
                model=s.llm_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"原文案:\n{text}"},
                ],
                temperature=0.7,
            )
            result = (completion.choices[0].message.content or "").strip()
            if not result:
                raise RuntimeError("LLM 返回空内容")
            logger.info(f"LLM 润色完成({s.llm_model}),{len(text)}字 → {len(result)}字")
            return result
        except Exception as e:
            # v1 行为:改写失败回退原文,不阻断流水线
            logger.warning(f"LLM 改写失败,回退原文: {e}")
            return text
