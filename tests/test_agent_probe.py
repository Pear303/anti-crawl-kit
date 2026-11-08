"""无浏览器测试：agent_probe 的页面状态判定。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anti_crawl_kit.tools.agent_probe import (  # noqa: E402
    _analyze_html,
    _build_script_hint,
    _diff_payload,
    _next_actions,
    _strip_tags,
)


def _analyze(html: str, *, status: int = 200, field: str = "fansNum",
             cookies=None, auth=None):
    return _analyze_html(
        html,
        status,
        "https://example.com/page",
        field=field,
        cookies=cookies or {},
        auth_cookies=auth or [],
    )


def test_strip_tags_removes_script_and_style():
    html = "<html><script>var x=1</script><style>.a{}</style><body>正文</body></html>"
    text = _strip_tags(html)
    assert "var x" not in text
    assert ".a{}" not in text
    assert "正文" in text


def test_html_has_data():
    result = _analyze("<html><title>正常</title><body><p>fansNum: 123</p></body></html>")
    assert result["state"] == "html_has_data"
    assert result["confidence"] == "high"
    assert result["field_in_html"] is True


def test_captcha_page():
    result = _analyze("<html><title>安全验证</title><body>请完成验证</body></html>")
    assert result["state"] == "captcha"
    assert result["confidence"] == "high"


def test_rate_limited_is_blocked():
    result = _analyze("<html></html>", status=429, field="")
    assert result["state"] == "blocked"
    assert result["reason"] == "http_429"


def test_login_wall_by_body():
    result = _analyze("<html><title>页面</title><body>请登录后查看</body></html>")
    assert result["state"] == "login_required"


def test_auth_cookie_missing():
    long_body = "内容" * 2000
    result = _analyze(
        f"<html><title>正常</title><body>{long_body}</body></html>",
        auth=["SESSDATA"],
    )
    assert result["state"] == "login_required"
    assert result["reason"] == "auth_cookie_missing"
    assert result["has_auth_cookie"] is False


def test_auth_cookie_present_passes():
    long_body = "内容" * 2000
    result = _analyze(
        f"<html><title>正常</title><body>{long_body}</body></html>",
        cookies={"SESSDATA": "x"},
        auth=["SESSDATA"],
    )
    assert result["state"] == "unknown"
    assert result["has_auth_cookie"] is True


def test_next_actions_for_blocked():
    actions = _next_actions("blocked", {}, [])
    assert any("不要重试" in a for a in actions)


def test_next_actions_for_js_api_includes_url():
    candidates = [{"url": "https://api.example.com/x/list", "method": "GET"}]
    actions = _next_actions("js_api", {}, candidates)
    assert any("https://api.example.com/x/list" in a for a in actions)


# ── 脚本草稿 ─────────────────────────────────────────
def test_script_hint_js_api():
    hint = _build_script_hint(
        "js_api",
        {},
        [{"url": "https://api.example.com/x/list", "method": "GET"}],
        page_url="https://example.com/page",
        field="fansNum",
    )
    assert hint["type"] == "http_json"
    assert "https://api.example.com/x/list" in hint["code"]


def test_script_hint_html_has_data():
    hint = _build_script_hint(
        "html_has_data",
        {},
        [],
        page_url="https://example.com/page",
        field="fansNum",
    )
    assert hint["type"] == "yaml_config"
    assert "card_selector" in hint["code"]


def test_script_hint_browser_needed():
    hint = _build_script_hint(
        "browser_needed",
        {},
        [],
        page_url="https://example.com/page",
        field="",
    )
    assert hint["type"] == "playwright_intercept"
    assert "capture_responses" in hint["code"]


def test_script_hint_unknown_returns_none():
    assert _build_script_hint("unknown", {}, [], page_url="https://x", field="") is None


# ── 基线对比 ─────────────────────────────────────────
def test_diff_decision_changed():
    base = {"decision": "js_api", "evidence": {}, "candidates": []}
    cur = {"decision": "captcha", "evidence": {}, "candidates": []}
    changes = _diff_payload(base, cur)
    assert any(c["type"] == "decision_changed" for c in changes)


def test_diff_candidates_changed():
    base = {
        "decision": "js_api",
        "evidence": {},
        "candidates": [{"url": "https://api.example.com/old"}],
    }
    cur = {
        "decision": "js_api",
        "evidence": {},
        "candidates": [{"url": "https://api.example.com/new"}],
    }
    changes = _diff_payload(base, cur)
    changed = [c for c in changes if c["type"] == "candidates_changed"][0]
    assert "https://api.example.com/new" in changed["added"]
    assert "https://api.example.com/old" in changed["removed"]


def test_diff_field_location_changed():
    base = {
        "decision": "js_api",
        "evidence": {"field_in_html": False},
        "candidates": [],
    }
    cur = {
        "decision": "html_has_data",
        "evidence": {"field_in_html": True},
        "candidates": [],
    }
    changes = _diff_payload(base, cur)
    assert any(c["type"] == "field_location_changed" for c in changes)


def test_diff_no_change():
    payload = {
        "decision": "js_api",
        "evidence": {"field_in_html": False},
        "candidates": [{"url": "https://api.example.com/x"}],
    }
    assert _diff_payload(payload, payload) == []
