"""无浏览器测试：FetchOutcome.stop_reason 与 WAF 识别。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.datasource import (
    DataSourcePolicy,
    StopReason,
    detect_waf,
    fetch_with_fallback,
)


def _policy(**kwargs) -> DataSourcePolicy:
    defaults = dict(primary="api", fallbacks=("html",), cross_check=False)
    defaults.update(kwargs)
    return DataSourcePolicy(**defaults)


def test_stop_reason_ok():
    outcome = fetch_with_fallback(
        {"api": lambda: [{"title": "x"}]},
        policy=_policy(),
    )
    assert outcome.ok is True
    assert outcome.stop_reason == StopReason.OK
    assert outcome.elapsed_ms >= 0


def test_stop_reason_blocked_with_cloudflare():
    def api():
        raise RuntimeError("403 Cloudflare Just a moment...")

    outcome = fetch_with_fallback(
        {"api": api, "html": lambda: [1]},
        policy=_policy(),
    )
    assert outcome.ok is False
    assert outcome.stop_reason == StopReason.BLOCKED
    assert outcome.detection == "cloudflare"
    assert outcome.attempts[0]["detection"] == "cloudflare"
    assert "elapsed_ms" in outcome.attempts[0]


def test_stop_reason_captcha_wins_over_waf():
    def api():
        raise RuntimeError("datadome captcha challenge")

    outcome = fetch_with_fallback({"api": api}, policy=_policy())
    assert outcome.stop_reason == StopReason.CAPTCHA


def test_stop_reason_empty():
    outcome = fetch_with_fallback(
        {"api": lambda: [], "html": lambda: []},
        policy=_policy(),
    )
    assert outcome.ok is False
    assert outcome.stop_reason == StopReason.EMPTY
    assert len(outcome.attempts) == 2


def test_stop_reason_no_fetcher():
    outcome = fetch_with_fallback({}, policy=_policy())
    assert outcome.ok is False
    assert outcome.stop_reason == StopReason.NO_FETCHER
    assert outcome.source == ""


def test_stop_reason_after_fallback_success():
    def api():
        raise RuntimeError("timeout")

    outcome = fetch_with_fallback(
        {"api": api, "html": lambda: [{"title": "x"}]},
        policy=_policy(),
    )
    assert outcome.ok is True
    assert outcome.source == "html"
    assert outcome.stop_reason == StopReason.OK


def test_mode_recorded(monkeypatch):
    monkeypatch.setenv("SOURCE_MODE", "html")
    outcome = fetch_with_fallback(
        {"api": lambda: [1], "html": lambda: [2]},
        policy=_policy(),
    )
    assert outcome.mode == "html"
    assert outcome.source == "html"


def test_as_dict_shape():
    outcome = fetch_with_fallback(
        {"api": lambda: [1, 2]},
        policy=_policy(),
    )
    data = outcome.as_dict()
    assert data["ok"] is True
    assert data["items"] == 2
    assert data["stop_reason"] == "ok"
    assert isinstance(data["attempts"], list)


def test_summary_contains_stop_reason():
    outcome = fetch_with_fallback({"api": lambda: []}, policy=_policy())
    text = outcome.summary()
    assert "stop_reason=empty" in text
    assert "elapsed=" in text


def test_detect_waf_markers():
    assert detect_waf("cf-chl-bypass") == "cloudflare"
    assert detect_waf("Reference #18.abc") == "akamai"
    assert detect_waf("datadome.co/block") == "datadome"
    assert detect_waf("tcaptcha") == "tencent"
    assert detect_waf("nothing here") == ""
    assert detect_waf("") == ""
