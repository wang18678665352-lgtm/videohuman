"""LLM 调用(OpenAI 兼容接口):rewrite / translate 等文本节点共用。"""

from __future__ import annotations

import json

from .config import Settings


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


def llm_configured(settings: Settings) -> bool:
    return clean_api_key(settings.llm_api_key) is not None


def chat(settings: Settings, system: str, user: str, temperature: float = 0.7) -> str:
    """单轮对话,返回去首尾空白的回复;未配置 key、请求失败或空回复都抛异常。"""
    from openai import OpenAI

    key = clean_api_key(settings.llm_api_key)
    if not key:
        raise RuntimeError("未配置 LLM api_key(环境变量 VH_LLM_API_KEY)")
    # config.toml/env 里常带着引号书写,统一剥掉;不设 base_url 时 SDK 默认走官方地址
    base_url = settings.llm_base_url.strip().strip("\"'") or None
    # 显式超时 + 有限重试:LLM 接口抖动不应让整条流水线卡死
    client = OpenAI(api_key=key, base_url=base_url, timeout=settings.llm_timeout, max_retries=2)
    completion = client.chat.completions.create(
        model=settings.llm_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
    )
    result = (completion.choices[0].message.content or "").strip()
    if not result:
        raise RuntimeError("LLM 返回空内容")
    return result
