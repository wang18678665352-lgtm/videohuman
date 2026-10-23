"""rewrite stage — LLM 文案改写(OpenAI 兼容接口,依赖注入,去全局状态)。

搬自 v1 ai_processing/text_rewriter.py:clean_api_key + 抖音改写 prompt 原样保留。
阶段 1 约定:未配置 llm_api_key 时**直通**(原文落盘),先打通主链路。
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from ..pipeline import Stage, StageContext

# v1 原样保留的抖音爆款改写 prompt(业务资产)
SYSTEM_PROMPT = """你是一名顶尖抖音爆款文案基因工程师，严格遵循以下协议执行文案改造：
1、**核心任务：** 基于原始文案，改写一篇高质量、高传播力的视频口播文案。
2、**内容忠实度：** 保持原始叙事时序与主题结构50%不变。
3、**清晰度与结构：** 叙事清晰，内容层次分明。改写后文案字数与原文接近。
4、**深度降重（原创保障）：**
    *   同义词链替换（三级跳转以上）。
    *   句式拓扑变形（陈述/疑问/感叹转换）。
    *   案例素材置换（80%替换率）。
    *   **确保重复率≤30%，输出文案属于原创。**
5. **抖音爆款结构：**
    *   **首行：** 必须植入强吸引力、高悬念的“黄金三秒”锚点。
    *   **互动激发：** 每3行设置一个UGC激发槽点（争议点、共鸣点、疑问点）。
6、**关键词策略（自然融入）：**
    *   自然埋入3个不同的垂类关键词。
    *   每个关键词在正文中出现2次（词频=2）。
    *   **严格禁止任何形式的关键词标注、说明或额外输出。**
7、**合规与表达：**
    *   严格遵循抖音违禁词库v2025.2。
    *   禁用绝对化表述（如“最”、“第一”、“绝对”等）。
8、**输出格式：**
    *   只输出最终改写完成的纯净中文文案正文，禁止任何解释/标注/问候。
    *   每行为独立语义单元，每行以中文逗号（，）结尾。
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
        # config.toml/env 里常带着引号书写,统一剥掉;不设 base_url 时 SDK 默认走官方地址
        base_url = s.llm_base_url.strip().strip("\"'") or None
        client = OpenAI(api_key=key, base_url=base_url)
        try:
            completion = client.chat.completions.create(
                model=s.llm_model,
                messages=[
                    {"role": "system", "content": "你是一个专业的文案修改助手"},
                    {"role": "user", "content": f"{SYSTEM_PROMPT}\n\n原文案:{text}"},
                ],
                temperature=0.7,
            )
            result = completion.choices[0].message.content.strip()
            logger.info(f"LLM 改写完成({s.llm_model}),{len(text)}字 → {len(result)}字")
            return result
        except Exception as e:
            # v1 行为:改写失败回退原文,不阻断流水线
            logger.warning(f"LLM 改写失败,回退原文: {e}")
            return text
