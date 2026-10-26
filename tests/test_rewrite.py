"""rewrite stage:提示词配置与 LLM 调用(用假 openai 模块,不发网络请求)。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from vh_core.config import Settings
from vh_core.pipeline import StageContext
from vh_core.stages.rewrite import DEFAULT_PROMPT, RewriteStage, load_prompt


def _ctx(settings: Settings, text: str = "原始文案") -> StageContext:
    job_dir = settings.workspace / "j"
    (job_dir / "rewrite").mkdir(parents=True)
    return StageContext(
        settings=settings, job_id="j", params={"text": text}, stage_dir=job_dir / "rewrite", job_dir=job_dir
    )


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> dict:
    seen: dict = {}

    class _Completions:
        def create(self, **kw):
            seen.update(kw)
            msg = types.SimpleNamespace(content=seen.get("_reply", "润色后的文案"))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    class OpenAI:
        def __init__(self, **kw):
            seen["client"] = kw
            self.chat = types.SimpleNamespace(completions=_Completions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=OpenAI))
    return seen


def test_default_prompt_is_compliant() -> None:
    assert "降重" not in DEFAULT_PROMPT and "重复率" not in DEFAULT_PROMPT
    assert "不改变原意" in DEFAULT_PROMPT


def test_load_prompt_from_file(settings: Settings, tmp_path: Path) -> None:
    assert load_prompt(settings) == DEFAULT_PROMPT
    f = tmp_path / "p.txt"
    f.write_text("  自定义提示词  \n", encoding="utf-8")
    settings.llm_prompt_file = str(f)
    assert load_prompt(settings) == "自定义提示词"


def test_load_prompt_missing_or_empty_file_fails(settings: Settings, tmp_path: Path) -> None:
    settings.llm_prompt_file = str(tmp_path / "nope.txt")
    with pytest.raises(FileNotFoundError):
        load_prompt(settings)
    (tmp_path / "empty.txt").write_text(" ", encoding="utf-8")
    settings.llm_prompt_file = str(tmp_path / "empty.txt")
    with pytest.raises(ValueError):
        load_prompt(settings)


def test_passthrough_without_api_key(settings: Settings) -> None:
    out = RewriteStage().run(_ctx(settings))["text"]
    assert out.read_text(encoding="utf-8") == "原始文案"


def test_llm_uses_configured_prompt(settings: Settings, fake_openai: dict) -> None:
    settings.llm_api_key = "sk-test"
    out = RewriteStage().run(_ctx(settings))["text"]
    assert out.read_text(encoding="utf-8") == "润色后的文案"
    msgs = fake_openai["messages"]
    assert msgs[0] == {"role": "system", "content": DEFAULT_PROMPT}
    assert "原始文案" in msgs[1]["content"]
    assert fake_openai["client"]["timeout"] == settings.llm_timeout


def test_bad_prompt_file_fails_stage_instead_of_silent_fallback(settings: Settings, fake_openai: dict) -> None:
    settings.llm_api_key = "sk-test"
    settings.llm_prompt_file = "does/not/exist.txt"
    with pytest.raises(FileNotFoundError):
        RewriteStage().run(_ctx(settings))


def test_empty_llm_reply_falls_back_to_original(settings: Settings, fake_openai: dict) -> None:
    settings.llm_api_key = "sk-test"
    fake_openai["_reply"] = "   "
    out = RewriteStage().run(_ctx(settings))["text"]
    assert out.read_text(encoding="utf-8") == "原始文案"
