"""纯文本/参数处理逻辑:字幕分句与 ASR 对齐、发布参数解析、平台限制、API key 清洗。"""

from __future__ import annotations

from datetime import datetime

import pytest

from vh_core.publishers.base import normalize_tags, truncate_title
from vh_core.stages.postprocess import _align_lines_to_asr, _ass_ts, _split_cover_text, split_subtitle_lines
from vh_core.stages.publish import _parse_publish_date, _parse_tags
from vh_core.stages.rewrite import clean_api_key


def test_split_subtitle_lines_by_punct_and_length() -> None:
    assert split_subtitle_lines("你好,世界。今天天气不错!") == ["你好", "世界", "今天天气不错"]
    long = "一" * 32
    assert split_subtitle_lines(long, max_len=15) == ["一" * 15, "一" * 15, "一" * 2]
    assert split_subtitle_lines("。。。\n\n") == []


def test_ass_timestamp_format() -> None:
    assert _ass_ts(0) == "0:00:00.00"
    assert _ass_ts(61.234) == "0:01:01.23"
    assert _ass_ts(3600) == "1:00:00.00"


def test_align_lines_to_asr_uses_word_timestamps() -> None:
    lines = ["你好", "世界"]
    words = ["你好", "世界"]
    ts = [[0, 1000], [1500, 2500]]
    segs = _align_lines_to_asr(lines, words, ts, duration=3.0)
    assert segs == [
        {"text": "你好", "start": 0.0, "end": 1.0},
        {"text": "世界", "start": 1.5, "end": 2.5},
    ]


def test_align_lines_to_asr_falls_back_on_low_match() -> None:
    assert _align_lines_to_asr(["完全不同的文本"], ["abc"], [[0, 100]], duration=1.0) == []
    assert _align_lines_to_asr([], [], [], duration=1.0) == []


def test_split_cover_text() -> None:
    assert _split_cover_text("第一行，第二行") == ("第一行", "第二行")
    assert _split_cover_text("first,second,third") == ("first", "second,third")
    assert _split_cover_text("单行") == ("单行", "")


def test_parse_tags() -> None:
    assert _parse_tags("a, b，c,,") == ["a", "b", "c"]
    assert _parse_tags(["x", " ", "y"]) == ["x", "y"]
    assert _parse_tags(None) == []
    with pytest.raises(ValueError):
        _parse_tags(123)


def test_parse_publish_date_is_timezone_aware() -> None:
    assert _parse_publish_date(None) is None
    dt = _parse_publish_date("2026-08-08T20:00:00")
    assert dt is not None and dt.tzinfo is not None
    assert (dt.hour, dt.minute) == (20, 0)  # 裸时间按本机时区解释,不平移
    aware = _parse_publish_date("2026-08-08T20:00:00+08:00")
    assert aware.utcoffset().total_seconds() == 8 * 3600
    assert _parse_publish_date(datetime(2026, 1, 1, 9)).tzinfo is not None
    with pytest.raises(ValueError):
        _parse_publish_date("not-a-date")


def test_platform_limits() -> None:
    assert len(truncate_title("douyin", "字" * 100)) == 55
    assert truncate_title("bilibili", "  标题  ") == "标题"
    tags = normalize_tags("douyin", ["#a", "a", " b ", "", "c" * 30] + [f"t{i}" for i in range(20)])
    assert tags[:3] == ["a", "b", "c" * 20]
    assert len(tags) == 10


def test_clean_api_key() -> None:
    assert clean_api_key(None) is None
    assert clean_api_key("  sk-abc ") == "sk-abc"
    assert clean_api_key('["sk-1", "sk-2"]') == "sk-1"
    assert clean_api_key("['sk-1'") == "['sk-1'"
    assert clean_api_key("   ") is None
