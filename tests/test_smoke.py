"""无浏览器冒烟测试 —— 验证纯逻辑模块（不启动 Chrome）。

运行::

    python -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.generic import FieldSpec, PlatformSpec, _build_extract_js
from anti_crawl_kit.risk import RiskKind, classify_http_result
from anti_crawl_kit.strategy import run_strategies
from anti_crawl_kit.text import (
    clean_anti_crawl_watermark,
    clean_html,
    make_dedupe_key,
    normalize_count,
    parse_count,
    safe_filename,
)


def test_parse_count():
    assert parse_count("2399.2万") == 23992000
    assert parse_count("1.2亿") == 120000000
    assert parse_count("1,234") == 1234
    assert parse_count("无效") == 0


def test_normalize_count():
    assert normalize_count("2.2万") == 22000
    assert normalize_count("") is None
    assert normalize_count("-") is None
    assert normalize_count("1,093") == 1093


def test_watermark():
    raw = "正常文本wordwordwordwordwordmmMwWLliI0fiflO&123\u200b结束"
    cleaned = clean_anti_crawl_watermark(raw)
    assert "word" not in cleaned
    assert "mmMwWLliI0fiflO" not in cleaned
    assert "\u200b" not in cleaned
    assert "正常文本" in cleaned and "结束" in cleaned


def test_clean_html():
    assert clean_html("<em>露营</em>&amp;徒步") == "露营&徒步"


def test_safe_filename():
    assert safe_filename('a/b:c*?"<>|') == "a_b_c______"
    assert safe_filename("", default_name="untitled") == "untitled"


def test_dedupe_key():
    assert make_dedupe_key("bilibili", "BV1xx") == "bilibili:BV1xx"
    assert (
        make_dedupe_key("douyin", url="https://www.douyin.com/video/123/?a=1")
        == "douyin:https://www.douyin.com/video/123"
    )
    assert make_dedupe_key("x", keyword="k", title="t", author_external_id="a") == "x:k:a:t"


def test_risk_classify():
    assert classify_http_result(200, "ok").kind == RiskKind.OK
    assert classify_http_result(429, "").kind == RiskKind.RATE_LIMITED
    assert classify_http_result(403, "").should_fallback_to_browser
    assert classify_http_result(200, "请登录").kind == RiskKind.LOGIN_REQUIRED
    assert classify_http_result(200, "请输入验证码").kind == RiskKind.CAPTCHA
    # 正常登录弹窗里的「验证码登录」不是验证码墙
    assert classify_http_result(200, "验证码登录 密码登录").kind == RiskKind.OK
    # 短 JSON 登录墙：状态码 200 也要识别
    assert (
        classify_http_result(200, '{"ok":-100,"url":"https://x.com/login.php"}').kind
        == RiskKind.LOGIN_REQUIRED
    )
    # 202 + 空 body：起点式反爬挑战
    decision = classify_http_result(202, "")
    assert decision.kind == RiskKind.BLOCKED
    assert decision.reason == "challenge_202"
    # 202 + JSON：正常异步任务，不判拦截
    assert classify_http_result(202, '{"task":"accepted"}').kind == RiskKind.OK
    # 长页面里的「请登录」按钮不是登录墙
    assert (
        classify_http_result(200, "正文内容。" * 500 + "请登录").kind
        == RiskKind.OK
    )
    # 正常大页面里的 signature 字样不是签名失效
    assert (
        classify_http_result(200, "正常内容。" * 500 + "a_bogus signature").kind
        == RiskKind.OK
    )


def test_run_strategies():
    results = run_strategies(
        [
            ("empty", lambda: []),
            ("boom", lambda: (_ for _ in ()).throw(ValueError("x"))),
            ("ok", lambda: [1, 2]),
        ],
        stop_on_first_items=True,
    )
    assert [r.name for r in results] == ["empty", "boom", "ok"]
    assert results[1].ok is False and results[2].items == [1, 2]


def test_platform_spec_from_dict():
    spec = PlatformSpec.from_dict(
        {
            "name": "demo",
            "start_url": "https://example.com",
            "card_selector": "div.item",
            "fields": {"title": {"selector": "h3"}, "url": {"selector": "a", "attr": "href"}},
            "scroll": {"times": 2},
        }
    )
    assert spec.name == "demo"
    assert spec.fields["url"].attr == "href"
    assert spec.scroll.times == 2
    js = _build_extract_js(spec)
    assert "querySelectorAll" in js


def test_field_spec_default():
    f = FieldSpec("h3")
    assert f.attr == "text" and f.all is False
