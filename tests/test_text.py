"""无浏览器测试：中文计数解析（parse_count / normalize_count）。"""
from __future__ import annotations

import time

import pytest

from anti_crawl_kit.text import normalize_count, parse_count

# 复合单位：按"数字+单位"分段累加，单位连乘（万亿 = 1e4 × 1e8）。
COMPOUND_CASES = [
    pytest.param("2亿300万", 203_000_000, id="yi-plus-wan"),
    pytest.param("1.5亿2000万", 170_000_000, id="decimal-yi-plus-wan"),
    pytest.param("1万亿", 1_000_000_000_000, id="wan-yi"),
    pytest.param("10万5千", 105_000, id="wan-plus-qian"),
    pytest.param("1亿零300万", 103_000_000, id="yi-ling-wan"),
]

# R-001 回归：数字与单位之间允许空白（含 NBSP / 全角空格）。
WHITESPACE_CASES = [
    pytest.param("1.2 亿", 120_000_000, id="space-yi"),
    pytest.param("1 234万", 12_340_000, id="thousand-grouped-wan"),
    pytest.param("2399.2 万", 23_992_000, id="space-wan"),
    pytest.param("1.2\u00a0亿", 120_000_000, id="nbsp-yi"),
    pytest.param("1.2\u3000亿", 120_000_000, id="ideographic-space-yi"),
]

# R-002 回归：只累加第一段连续计数，段间只允许"零"与空白。
SEGMENT_CASES = [
    pytest.param("销量2亿，营收3万", 200_000_000, id="two-counts-comma"),
    pytest.param("5万～10万", 50_000, id="range-wan"),
    pytest.param("2亿-3万", 200_000_000, id="two-counts-dash"),
    pytest.param("1亿abc2万", 100_000_000, id="text-between"),
]

# R-003 回归：Decimal 精确乘法，避免 float 截断少 1。
PRECISION_CASES = [
    pytest.param("1.13万", 11_300, id="wan-two-decimals"),
    pytest.param("2.03百", 203, id="bai-two-decimals"),
    pytest.param("1.13亿", 113_000_000, id="yi-two-decimals"),
]

# SEC-001 / SEC-002 回归：超长输入、超长单位串判无效且不抛异常。
PARSE_COUNT_INVALID_CASES = [
    pytest.param("9" + "亿" * 40, 0, id="unit-run-overflow"),
    pytest.param("1" * 20000 + "a" + "万", 0, id="long-digit-dos"),
    pytest.param("1" * 1001, 0, id="input-too-long"),
]

NORMALIZE_COUNT_INVALID_CASES = [
    pytest.param("9" + "亿" * 40, None, id="unit-run-overflow"),
    pytest.param("1" * 20000 + "a" + "万", None, id="long-digit-dos"),
    pytest.param("1" * 1001, None, id="input-too-long"),
]

PARSE_COUNT_REGRESSION_CASES = [
    pytest.param("2399.2万", 23_992_000, id="decimal-wan"),
    pytest.param("1.0万", 10_000, id="one-wan"),
    pytest.param("1.2亿", 120_000_000, id="decimal-yi"),
    pytest.param("531", 531, id="plain-integer"),
    pytest.param("1,234", 1_234, id="comma-integer"),
    pytest.param("-", 0, id="dash"),
    pytest.param("abc", 0, id="letters"),
    pytest.param("2.5", 0, id="bare-decimal"),
]

NORMALIZE_COUNT_REGRESSION_CASES = [
    pytest.param("2399.2万", 23_992_000, id="decimal-wan"),
    pytest.param("1.0万", 10_000, id="one-wan"),
    pytest.param("1.2亿", 120_000_000, id="decimal-yi"),
    pytest.param("531", 531, id="plain-integer"),
    pytest.param("1,234", 1_234, id="comma-integer"),
    pytest.param(None, None, id="none"),
    pytest.param("", None, id="empty"),
    pytest.param("-", None, id="dash"),
    pytest.param("abc", None, id="letters"),
    pytest.param("2.5", None, id="bare-decimal"),
]


@pytest.mark.parametrize(("text", "expected"), COMPOUND_CASES)
def test_parse_count_compound_units(text: str, expected: int) -> None:
    assert parse_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), COMPOUND_CASES)
def test_normalize_count_compound_units(text: str, expected: int) -> None:
    assert normalize_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), WHITESPACE_CASES)
def test_parse_count_tolerates_whitespace(text: str, expected: int) -> None:
    assert parse_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), WHITESPACE_CASES)
def test_normalize_count_tolerates_whitespace(text: str, expected: int) -> None:
    assert normalize_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), SEGMENT_CASES)
def test_parse_count_stops_at_non_separator(text: str, expected: int) -> None:
    assert parse_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), SEGMENT_CASES)
def test_normalize_count_stops_at_non_separator(text: str, expected: int) -> None:
    assert normalize_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), PRECISION_CASES)
def test_parse_count_decimal_precision(text: str, expected: int) -> None:
    assert parse_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), PRECISION_CASES)
def test_normalize_count_decimal_precision(text: str, expected: int) -> None:
    assert normalize_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), PARSE_COUNT_INVALID_CASES)
def test_parse_count_invalid_input(text: str, expected: int) -> None:
    assert parse_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), NORMALIZE_COUNT_INVALID_CASES)
def test_normalize_count_invalid_input(text: str, expected: None) -> None:
    assert normalize_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), PARSE_COUNT_REGRESSION_CASES)
def test_parse_count_existing_behavior(text: str, expected: int) -> None:
    assert parse_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), NORMALIZE_COUNT_REGRESSION_CASES)
def test_normalize_count_existing_behavior(text: str | None, expected: int | None) -> None:
    assert normalize_count(text) == expected


def test_parse_count_unit_run_at_limit() -> None:
    """8 个连续单位仍在允许范围内（亿 × 8 = 1e64）。"""
    assert parse_count("1" + "亿" * 8) == 10**64


def test_parse_count_long_input_is_fast() -> None:
    """SEC-001：超长数字段 + 单位必须快速返回，不得 O(n²) 回溯。"""
    text = "1" * 20000 + "a" + "万"
    start = time.perf_counter()
    result = parse_count(text)
    elapsed = time.perf_counter() - start
    assert result == 0
    assert elapsed < 2.0, f"parse_count 处理超长输入耗时 {elapsed:.3f}s"


# ── clean_html / safe_filename / make_dedupe_key ────────────────


def test_clean_html_strips_tags_and_unescapes() -> None:
    from anti_crawl_kit.text import clean_html

    assert clean_html("<em>关键词</em> &amp; 更多") == "关键词 & 更多"
    assert clean_html(None) == ""
    assert clean_html("") == ""


def test_clean_html_removes_anti_crawl_watermark() -> None:
    from anti_crawl_kit.text import clean_html

    zero_width = "正常\u200b文本"
    assert clean_html(zero_width) == "正常文本"
    assert clean_html("前mmMwWLliI0fiflO&1后") == "前后"
    assert clean_html("wordwordwordwordword正文") == "正文"


def test_clean_anti_crawl_watermark_keeps_normal_words() -> None:
    from anti_crawl_kit.text import clean_anti_crawl_watermark

    assert clean_anti_crawl_watermark("word word word") == "word word word"
    assert clean_anti_crawl_watermark("") == ""


def test_safe_filename_replaces_illegal_chars() -> None:
    from anti_crawl_kit.text import safe_filename

    assert safe_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert safe_filename("  .name.  ") == "name"
    assert safe_filename("", default_name="fallback") == "fallback"
    assert safe_filename("...", default_name="fallback") == "fallback"
    assert safe_filename("abcdef", max_len=3) == "abc"
    assert safe_filename("ab . cd", max_len=3) == "ab"


def test_make_dedupe_key_priority() -> None:
    from anti_crawl_kit.text import make_dedupe_key

    # 1. platform + external_id 最优先
    assert make_dedupe_key("bilibili", "BV1xx", "https://b23.tv/x") == "bilibili:BV1xx"

    # 2. 没有 id 时用规范化 URL（去掉 query/fragment/末尾斜杠）
    assert (
        make_dedupe_key("weibo", "", "https://weibo.com/a/b/?from=timeline#top")
        == "weibo:https://weibo.com/a/b"
    )

    # 3. 最后才用 keyword:author:title
    assert (
        make_dedupe_key("zhihu", "", "", keyword="爬虫", title="标题", author_external_id="u1")
        == "zhihu:爬虫:u1:标题"
    )

    # 三者缺一 → None
    assert make_dedupe_key("zhihu", "", "", keyword="爬虫") is None
    assert make_dedupe_key("", "", "https://x.test/a") is None
    assert make_dedupe_key("", "id1") is None
