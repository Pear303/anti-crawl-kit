"""无浏览器测试：数据源分层兜底 + 双跑校验。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.datasource import (
    DataSourcePolicy,
    classify_exception,
    compare_items,
    fetch_with_fallback,
    resolve_source_mode,
)


def _policy(**kwargs) -> DataSourcePolicy:
    defaults = dict(primary="api", fallbacks=("html",), cross_check=False)
    defaults.update(kwargs)
    return DataSourcePolicy(**defaults)


# ── 主路径 / 回退 ────────────────────────────────────
def test_primary_ok():
    outcome = fetch_with_fallback(
        {"api": lambda: [1, 2], "html": lambda: [3]},
        policy=_policy(),
    )
    assert outcome.ok is True
    assert outcome.source == "api"
    assert outcome.items == [1, 2]


def test_fallback_on_network_error():
    def api():
        raise RuntimeError("timeout")

    outcome = fetch_with_fallback(
        {"api": api, "html": lambda: [{"title": "x"}]},
        policy=_policy(),
    )
    assert outcome.ok is True
    assert outcome.source == "html"
    assert outcome.attempts[0]["risk"] == "network"


def test_fallback_on_empty_result():
    outcome = fetch_with_fallback(
        {"api": lambda: [], "html": lambda: [{"title": "x"}]},
        policy=_policy(),
    )
    assert outcome.ok is True
    assert outcome.source == "html"
    assert outcome.attempts[0]["risk"] == "empty"


def test_no_fallback_on_blocked():
    def api():
        raise RuntimeError("HTTP 403 forbidden")

    outcome = fetch_with_fallback(
        {"api": api, "html": lambda: [1]},
        policy=_policy(),
    )
    assert outcome.ok is False
    assert outcome.source == "api"
    assert outcome.attempts[0]["risk"] == "blocked"


def test_no_fallback_on_captcha():
    def api():
        raise RuntimeError("命中验证码")

    outcome = fetch_with_fallback(
        {"api": api, "html": lambda: [1]},
        policy=_policy(),
    )
    assert outcome.ok is False
    assert outcome.attempts[0]["risk"] == "captcha"


def test_signature_failed_goes_to_browser():
    def api():
        raise RuntimeError("a_bogus signature_failed")

    calls: list[str] = []

    def html():
        calls.append("html")
        return [{"title": "html"}]

    def browser():
        calls.append("browser")
        return [{"title": "browser"}]

    outcome = fetch_with_fallback(
        {"api": api, "html": html, "browser": browser},
        policy=_policy(),
    )
    assert outcome.ok is True
    assert outcome.source == "browser"
    assert calls == ["browser"]   # HTML 被跳过


def test_source_mode_override(monkeypatch):
    monkeypatch.setenv("SOURCE_MODE", "html")
    calls: list[str] = []

    def api():
        calls.append("api")
        return [1]

    def html():
        calls.append("html")
        return [2]

    outcome = fetch_with_fallback(
        {"api": api, "html": html},
        policy=_policy(),
    )
    assert outcome.ok is True
    assert outcome.source == "html"
    assert calls == ["html"]


def test_resolve_source_mode_default(monkeypatch):
    monkeypatch.delenv("SOURCE_MODE", raising=False)
    assert resolve_source_mode(DataSourcePolicy()) == ""


# ── 双跑校验 ─────────────────────────────────────────
def test_cross_check_alarm():
    outcome = fetch_with_fallback(
        {
            "api": lambda: [{"title": "旧标题"}],
            "html": lambda: [{"title": "新标题"}],
        },
        policy=_policy(cross_check=True, diff_threshold=0.1),
    )
    assert outcome.ok is True
    assert outcome.cross_check is not None
    assert outcome.cross_check["alarm"] is True
    assert outcome.cross_check["ratio"] == 1.0


def test_cross_check_normal():
    outcome = fetch_with_fallback(
        {
            "api": lambda: [{"title": "一样的"}],
            "html": lambda: [{"title": "一样的"}],
        },
        policy=_policy(cross_check=True),
    )
    assert outcome.cross_check["alarm"] is False
    assert outcome.cross_check["ratio"] == 0.0


def test_cross_check_disabled():
    outcome = fetch_with_fallback(
        {"api": lambda: [{"title": "a"}], "html": lambda: [{"title": "b"}]},
        policy=_policy(cross_check=False),
    )
    assert outcome.cross_check is None


def test_compare_items_ratio():
    result = compare_items(
        [{"title": "a"}, {"title": "b"}],
        [{"title": "a"}, {"title": "c"}],
        limit=5,
    )
    assert result["compared"] == 2
    assert result["mismatches"] == 1
    assert result["ratio"] == 0.5


def test_compare_items_skips_empty_fields():
    result = compare_items([{"title": ""}], [{"title": "x"}], limit=5)
    assert result["compared"] == 0
    assert result["ratio"] == 0.0


# ── 异常分类 ─────────────────────────────────────────
def test_classify_exception():
    assert classify_exception(RuntimeError("HTTP 403")) == "blocked"
    assert classify_exception(RuntimeError("HTTP 429")) == "rate_limited"
    assert classify_exception(RuntimeError("timeout")) == "network"
    assert classify_exception(RuntimeError("a_bogus failed")) == "signature_failed"
    assert classify_exception(RuntimeError("命中验证码")) == "captcha"
    assert classify_exception(RuntimeError("something else")) == "unknown"
