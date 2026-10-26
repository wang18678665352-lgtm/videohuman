"""文案相关的纯逻辑:字幕断句、改写、发布标题和标签。

这些函数决定观众看到的字。输入输出都是字符串,不需要视频,也不需要密钥。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vh_core.config import load_settings
from vh_core.media import _split_cover_text as split_cover_media
from vh_core.media import even_subtitle_segments, split_subtitle_lines, write_ass
from vh_core.models import StageStatus
from vh_core.pipeline import Pipeline, StageContext
from vh_core.publishers.base import normalize_tags, truncate_title
from vh_core.publishers.bilibili import BilibiliPublisher
from vh_core.stages.postprocess import _align_lines_to_asr
from vh_core.stages.postprocess import _split_cover_text as split_cover_stage
from vh_core.stages.postprocess import split_subtitle_lines as split_subtitle_stage
from vh_core.stages.publish import PublishStage, _default_title, _parse_publish_date, _parse_tags
from vh_core.stages.rewrite import RewriteStage, clean_api_key
from vh_core.stages.tts import TTSStage, _clean_ref_text


@pytest.mark.parametrize("split", [split_subtitle_lines, split_subtitle_stage])
def test_chinese_punctuation_splits_and_is_stripped(split) -> None:
    """中文口播用全角标点。断句后句尾的标点本身不出现在字幕上。

    media.py 和后期 stage 各有一份实现,两份必须切出同样的结果。
    """
    assert split("你好，世界。") == ["你好", "世界"]
    assert split("第一句！第二句？") == ["第一句", "第二句"]
    assert split("半角,ok!yes?") == ["半角", "ok", "yes"]
    assert split("第一行\n第二行，尾巴。") == ["第一行", "第二行", "尾巴"]
    assert split("  。。。  ") == []
    assert split("") == []


@pytest.mark.parametrize("split", [split_subtitle_lines, split_subtitle_stage])
def test_long_line_is_cut_without_losing_characters(split) -> None:
    """没有标点的长句按 15 个字硬切,拼回去必须还是原文,不能丢字也不能多字。"""
    text = "无标点的一长串文字超过十五个字就该被硬切掉后面继续"
    lines = split(text, max_len=15)
    assert "".join(lines) == text
    assert len(lines) >= 2
    assert all(len(line) <= 15 for line in lines)


def test_even_subtitles_and_ass_timestamp(tmp_path: Path) -> None:
    """没有语音时间轴时,几句字幕平分整段视频;ASS 时间写成 h:mm:ss.cc。"""
    segments = even_subtitle_segments(["上一句", "下一句"], 4)
    assert segments == [
        {"text": "上一句", "start": 0, "end": 2},
        {"text": "下一句", "start": 2, "end": 4},
    ]
    assert even_subtitle_segments([], 4) == []

    ass = tmp_path / "sub.ass"
    write_ass([{"text": "你好", "start": 1.5, "end": 3}], ass, "TestFont", 44, 1080, 1920)
    content = ass.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "0:00:01.50" in content
    assert "你好" in content


def test_asr_alignment_uses_word_timestamps_and_tolerates_length_mismatch() -> None:
    """字幕文字用原文,时间向 ASR 词戳借。词比时间戳多时不能抛异常。"""
    segments = _align_lines_to_asr(["你好", "世界"], ["你好", "世界"], [[0, 1000], [1000, 2000]], 2)
    assert [item["text"] for item in segments] == ["你好", "世界"]
    assert segments[0]["start"] == 0
    assert segments[0]["end"] == 1
    assert segments[1]["start"] == 1
    assert segments[1]["end"] == 2

    # 第二个词没有时间戳。截断后仍能给「你好」配上时间,而不是整段失败。
    partial = _align_lines_to_asr(["你好"], ["你好", "多余"], [[0, 800]], 2)
    assert partial[0]["text"] == "你好"
    assert partial[0]["start"] == 0
    assert partial[0]["end"] == 0.8

    # 原文和识别结果几乎对不上时,交回调用方去均分,这里返回空列表。
    assert _align_lines_to_asr(["完全不同"], ["你好"], [[0, 1000]], 1) == []


@pytest.mark.parametrize("split_cover", [split_cover_media, split_cover_stage])
def test_cover_text_splits_on_first_comma(split_cover) -> None:
    """封面两行字:遇到第一个中文或英文逗号切开,后面再有逗号也留在第二行。"""
    assert split_cover("第一行，第二行，还是第二行") == ("第一行", "第二行，还是第二行")
    assert split_cover("第一行,第二行") == ("第一行", "第二行")
    assert split_cover("只有一行") == ("只有一行", "")


def test_clean_api_key_accepts_plain_text_and_json_list() -> None:
    """密钥可能是纯字符串,也可能是老版本留下的 JSON 列表,只取第一把。"""
    assert clean_api_key(None) is None
    assert clean_api_key("   ") is None
    assert clean_api_key("  sk-abc  ") == "sk-abc"
    assert clean_api_key('["sk-abc", "sk-other"]') == "sk-abc"
    # 方括号但不是合法 JSON 时,把括号剥掉,剩下的当密钥。
    assert clean_api_key("[not-json]") == "not-json"


def test_rewrite_calls_llm_and_falls_back_to_original_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """配了密钥就调用模型;模型报错时退回原文,不让整条流水线停掉。"""
    seen: dict[str, str] = {}

    class _FakeClient:
        """最小的假客户端,形状和 openai.OpenAI(...).chat.completions.create 一样。"""

        def __init__(self, *, api_key: str, base_url: str | None) -> None:
            seen["api_key"] = api_key
            seen["base_url"] = base_url or ""
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            seen["model"] = kwargs["model"]
            if seen.get("fail"):
                raise RuntimeError("接口超时")

            # 真实 SDK 返回 completion.choices[0].message.content。这里用三层小对象模仿这个形状。
            class _Reply:
                def __init__(self) -> None:
                    self.content = "  改写后，的文案。  "

            class _Choice:
                def __init__(self) -> None:
                    self.message = _Reply()

            class _Completion:
                def __init__(self) -> None:
                    self.choices = [_Choice()]

            return _Completion()

    import openai

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    settings = load_settings(
        workspace_dir=tmp_path,
        llm_api_key='["sk-test"]',
        llm_base_url="  'https://example.test'  ",
        llm_model="demo-model",
    )
    stage = RewriteStage()
    ctx = _rewrite_ctx(tmp_path, settings, {"text": "原文"})
    outputs = stage.run(ctx)
    assert outputs["text"].read_text(encoding="utf-8") == "改写后，的文案。"
    assert seen["api_key"] == "sk-test"
    assert seen["base_url"] == "https://example.test"
    assert seen["model"] == "demo-model"

    seen["fail"] = "yes"
    again = stage.run(_rewrite_ctx(tmp_path, settings, {"text": "原文"}))
    assert again["text"].read_text(encoding="utf-8") == "原文"


def test_rewrite_enabled_rules(tmp_path: Path) -> None:
    """直接给了驱动音频就不用改写;既没有文案也没有转写结果,同样跳过。"""
    settings = load_settings(workspace_dir=tmp_path, llm_api_key="")
    stage = RewriteStage()
    assert stage.enabled(_rewrite_ctx(tmp_path, settings, {"audio": "voice.wav"})) is False
    assert stage.enabled(_rewrite_ctx(tmp_path, settings, {})) is False
    assert stage.enabled(_rewrite_ctx(tmp_path, settings, {"text": "有字"})) is True


def test_clean_ref_text_and_resolve_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """参考音文本:参数优先,其次同名 txt,再其次才是 ASR。情感标记和 emoji 要滤掉。"""
    assert _clean_ref_text("  你好<|NEUTRAL|>世界😊  ") == "你好世界"

    settings = load_settings(workspace_dir=tmp_path)
    stage_dir = tmp_path / "job" / "tts"
    stage_dir.mkdir(parents=True)
    ctx = StageContext(
        settings=settings,
        job_id="job",
        params={"ref_text": "  直接给的文本  "},
        stage_dir=stage_dir,
        job_dir=tmp_path / "job",
    )
    stage = TTSStage()
    assert stage._resolve_ref_text(ctx, None) == "直接给的文本"

    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"")
    audio.with_suffix(".txt").write_text("旁白文本\n", encoding="utf-8")
    ctx.params = {}
    assert stage._resolve_ref_text(ctx, audio) == "旁白文本"

    # 既不是默认参考音,也没有旁边的 txt,才调用 ASR。这里用假的识别器,不加载模型。
    other = tmp_path / "other.wav"
    other.write_bytes(b"")

    class _FakeAsr:
        def transcribe(self, audio_path, **kwargs):
            assert audio_path == other
            return {"text": "识别<|HAPPY|>结果"}

    monkeypatch.setattr("vh_core.stages.tts.get_provider", lambda *args, **kwargs: _FakeAsr())
    assert stage._resolve_ref_text(ctx, other) == "识别结果"
    assert (stage_dir / "ref_text.txt").read_text(encoding="utf-8") == "识别结果"


def test_publish_title_tags_and_local_datetime() -> None:
    """发布标题默认取文案第一句;标签接受中英文逗号;没写时区的时间按本机时区理解。"""
    job_dir = Path("unused")
    ctx = StageContext(
        settings=load_settings(),
        job_id="兜底标题",
        params={},
        stage_dir=job_dir,
        job_dir=job_dir,
    )
    # 没有文案文件时,标题退回 job_id。这个 ctx 的目录并不存在,函数只在文件在时才去读。
    assert _default_title(ctx) == "兜底标题"

    assert _parse_tags(None) == []
    assert _parse_tags(" 口播，测试, 口播 ,, ") == ["口播", "测试", "口播"]
    assert _parse_tags([" 数字人 ", "", "字幕"]) == ["数字人", "字幕"]
    with pytest.raises(ValueError, match="publish_tags"):
        _parse_tags(3)

    parsed = _parse_publish_date("2026-08-08T20:00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.hour == 20
    aware = _parse_publish_date("2026-08-08T12:00:00+00:00")
    assert aware is not None
    assert aware.utcoffset() == timedelta(0)
    assert _parse_publish_date(None) is None
    assert _parse_publish_date(datetime(2026, 8, 8, tzinfo=UTC)).tzinfo is UTC
    with pytest.raises(ValueError, match="publish_date"):
        _parse_publish_date("明天晚上")
    with pytest.raises(ValueError, match="publish_date"):
        _parse_publish_date(123)


def test_platform_title_and_tag_limits() -> None:
    """抖音标题最多 55 字;标签去掉 #、去重,并限制条数。"""
    assert truncate_title("douyin", "  短标题  ") == "短标题"
    long_title = "字" * 60
    assert len(truncate_title("douyin", long_title)) == 55
    assert len(truncate_title("bilibili", long_title)) == 60

    tags = normalize_tags("douyin", [" #口播 ", "口播", "", "数字人", "字幕"])
    assert tags == ["口播", "数字人", "字幕"]
    too_many = [f"标签{i}" for i in range(15)]
    assert len(normalize_tags("douyin", too_many)) == 10
    assert len(normalize_tags("bilibili", too_many)) == 12
    assert normalize_tags("douyin", ["#" + "标" * 25]) == ["标" * 20]


def test_bilibili_cookie_file_is_checked_before_upload(tmp_path: Path) -> None:
    """B 站登录文件在真正上传前先检查。缺文件、坏 JSON、缺字段都要给出能看懂的错误。"""
    publisher = BilibiliPublisher()
    missing = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError, match="未找到 B站登录态"):
        publisher.validate_cookies(missing)

    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="不是有效 JSON"):
        publisher.validate_cookies(bad)

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(
        json_cookies([]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"cookie_info\.cookies"):
        publisher.validate_cookies(incomplete)

    partial = tmp_path / "partial.json"
    partial.write_text(
        json_cookies([{"name": "SESSDATA", "value": "s"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bili_jct"):
        publisher.validate_cookies(partial)

    good = tmp_path / "good.json"
    good.write_text(
        json_cookies(
            [
                {"name": "SESSDATA", "value": "s"},
                {"name": "bili_jct", "value": "j"},
                {"name": "DedeUserID", "value": "1"},
            ],
            access_token="tok",
        ),
        encoding="utf-8",
    )
    publisher.validate_cookies(good)
    keys = publisher.extract_keys(good)
    assert keys["SESSDATA"] == "s"
    assert keys["access_token"] == "tok"


def test_publish_stage_records_each_platform_and_fails_only_when_all_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一个平台失败不耽误另一个平台;全部失败才抛错。这里的发布器是假的,不会打开浏览器。"""
    import vh_core.stages.publish as publish_mod

    class _Ok:
        def upload(self, video, title, tags, cover, cookies_path, publish_date=None):
            assert video.name == "final.mp4"
            assert title == "第一句"
            assert tags == ["口播"]
            return {"platform": "bilibili", "ok": True, "detail": "假装上传成功"}

    class _Bad:
        def upload(self, video, title, tags, cover, cookies_path, publish_date=None):
            raise RuntimeError("登录过期")

    monkeypatch.setitem(publish_mod.PUBLISHERS, "bilibili", _Ok)
    monkeypatch.setitem(publish_mod.PUBLISHERS, "douyin", _Bad)

    job_dir = tmp_path / "job"
    (job_dir / "postprocess").mkdir(parents=True)
    (job_dir / "postprocess" / "final.mp4").write_bytes(b"not a real video")
    (job_dir / "rewrite").mkdir()
    (job_dir / "rewrite" / "text.txt").write_text("第一句，第二句。\n", encoding="utf-8")
    stage_dir = job_dir / "publish"
    stage_dir.mkdir()
    ctx = StageContext(
        settings=load_settings(workspace_dir=tmp_path),
        job_id="job",
        params={"publish_platforms": ["bilibili", "douyin", "unknown"], "publish_tags": "口播"},
        stage_dir=stage_dir,
        job_dir=job_dir,
    )
    result = PublishStage().run(ctx)
    report = json_load(result["result"])
    assert report["title"] == "第一句"
    assert [item["platform"] for item in report["results"]] == ["bilibili", "douyin", "unknown"]
    assert report["results"][0]["ok"] is True
    assert report["results"][1]["ok"] is False
    assert "登录过期" in report["results"][1]["detail"]
    assert report["results"][2]["ok"] is False

    # 只留下会失败的平台时,结果文件仍然写出来,然后抛错。
    ctx.params["publish_platforms"] = ["douyin"]
    with pytest.raises(RuntimeError, match="所有平台发布均失败"):
        PublishStage().run(ctx)
    assert (stage_dir / "result.json").exists()


def test_publish_stage_is_disabled_without_platforms(tmp_path: Path) -> None:
    """没写要发到哪个平台,发布环节直接跳过。"""
    settings = load_settings(workspace_dir=tmp_path)
    manifest = Pipeline([PublishStage()], settings).run("job", params={})
    assert manifest.stages["publish"].status == StageStatus.SKIPPED


def _rewrite_ctx(tmp_path: Path, settings, params: dict) -> StageContext:
    job_dir = tmp_path / "rewrite-job"
    stage_dir = job_dir / "rewrite"
    stage_dir.mkdir(parents=True, exist_ok=True)
    return StageContext(
        settings=settings,
        job_id="rewrite-job",
        params=params,
        stage_dir=stage_dir,
        job_dir=job_dir,
    )


def json_cookies(cookies: list[dict], access_token: str = "") -> str:
    return json.dumps(
        {"cookie_info": {"cookies": cookies}, "token_info": {"access_token": access_token}},
        ensure_ascii=False,
    )


def json_load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
