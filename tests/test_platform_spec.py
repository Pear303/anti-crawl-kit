"""无浏览器测试：平台能力描述 + 登录态三态。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.platform_spec import (
    CHROME_EPOCH_OFFSET,
    CookieState,
    IdentityState,
    PlatformCapability,
    PlatformSpecRegistry,
    SpecConsistencyError,
    check_derived_consistency,
    check_identity,
    cookie_expired,
    derived_view,
    eval_cookie_state,
    normalize_handle,
    should_block_task,
    validate_spec,
)


def _webkit_ts(unix_ts: float) -> int:
    """Unix 秒 → Chrome WebKit 微秒。"""
    return int((unix_ts + CHROME_EPOCH_OFFSET) * 1_000_000)


# ── cookie 三态 ──────────────────────────────────────
def test_missing_when_no_cookie():
    state, reason = eval_cookie_state([], ("SESSDATA",))
    assert state is CookieState.MISSING
    assert reason == "no_matching_cookie"


def test_missing_when_auth_names_empty():
    state, reason = eval_cookie_state([{"name": "a", "value": "1"}], ())
    assert state is CookieState.MISSING
    assert reason == "no_auth_names_configured"


def test_valid_with_unexpired_cookie():
    now = time.time()
    cookies = [{"name": "SESSDATA", "value": "x", "expires": now + 3600}]
    state, _ = eval_cookie_state(cookies, ("SESSDATA",), now=now)
    assert state is CookieState.VALID


def test_expired_with_past_cookie():
    now = time.time()
    cookies = [{"name": "SESSDATA", "value": "x", "expires": now - 3600}]
    state, reason = eval_cookie_state(cookies, ("SESSDATA",), now=now)
    assert state is CookieState.EXPIRED
    assert reason == "all_expired"


def test_valid_if_any_cookie_valid():
    now = time.time()
    cookies = [
        {"name": "a", "value": "1", "expires": now - 10},
        {"name": "b", "value": "2", "expires": now + 3600},
    ]
    state, _ = eval_cookie_state(cookies, ("a", "b"), now=now)
    assert state is CookieState.VALID


def test_session_cookie_never_expires():
    now = time.time()
    cookies = [{"name": "SESSDATA", "value": "x", "expires": -1}]
    state, _ = eval_cookie_state(cookies, ("SESSDATA",), now=now)
    assert state is CookieState.VALID


def test_webkit_expires_utc_expired():
    now = time.time()
    cookies = [
        {"name": "SESSDATA", "value": "x", "expires_utc": _webkit_ts(now - 3600)}
    ]
    state, _ = eval_cookie_state(cookies, ("SESSDATA",), now=now)
    assert state is CookieState.EXPIRED


def test_webkit_expires_utc_valid():
    now = time.time()
    cookies = [
        {"name": "SESSDATA", "value": "x", "expires_utc": _webkit_ts(now + 3600)}
    ]
    state, _ = eval_cookie_state(cookies, ("SESSDATA",), now=now)
    assert state is CookieState.VALID


def test_empty_value_counts_as_missing():
    now = time.time()
    cookies = [{"name": "SESSDATA", "value": "", "expires": now + 3600}]
    state, _ = eval_cookie_state(cookies, ("SESSDATA",), now=now)
    assert state is CookieState.MISSING


def test_cookie_expired_missing_fields_is_false():
    assert cookie_expired({}) is False


# ── handle 规范化 ────────────────────────────────────
def test_normalize_handle_strips_known_prefixes():
    assert normalize_handle("mid:12345") == "12345"
    assert normalize_handle("uid: abc") == "abc"
    assert normalize_handle("score:99") == "score:99"   # 未知前缀保留
    assert normalize_handle(None) == ""


def test_check_identity_match_after_normalization():
    assert check_identity("mid:123", "123") is IdentityState.MATCH
    assert check_identity("123", "mid:123") is IdentityState.MATCH


def test_check_identity_mismatch():
    assert check_identity("123", "456") is IdentityState.MISMATCH


def test_check_identity_unknown_when_one_side_empty():
    assert check_identity("", "123") is IdentityState.UNKNOWN
    assert check_identity("123", None) is IdentityState.UNKNOWN


# ── 阻断规则 ────────────────────────────────────────
def test_block_when_identity_mismatch():
    block, msg = should_block_task(
        CookieState.VALID, IdentityState.MISMATCH, is_publish=False
    )
    assert block is True
    assert "不匹配" in msg


def test_collect_passes_when_missing():
    block, _ = should_block_task(
        CookieState.MISSING, IdentityState.UNKNOWN, is_publish=False
    )
    assert block is False


def test_publish_blocks_when_missing():
    block, _ = should_block_task(
        CookieState.MISSING, IdentityState.UNKNOWN, is_publish=True
    )
    assert block is True


def test_valid_and_match_passes():
    block, msg = should_block_task(
        CookieState.VALID, IdentityState.MATCH, is_publish=True
    )
    assert block is False
    assert msg == ""


# ── 能力描述 ────────────────────────────────────────
def test_platform_capability_roundtrip():
    cap = PlatformCapability(
        platform="demo",
        label="示例",
        auth_cookies=("sessionid",),
        known_unavailable={"play_count": "不提供"},
    )
    data = cap.to_dict()
    assert data["platform"] == "demo"
    assert data["auth_cookies"] == ["sessionid"]
    assert data["known_unavailable"]["play_count"] == "不提供"


# ── v0.3.0：单一来源 + 派生一致性 ──────────────────────
def _full_spec() -> PlatformCapability:
    return PlatformCapability(
        platform="demo",
        supports_login=True,
        auth_cookies=("sessionid", "sid_tt"),
        collector_module="mypkg.collectors.demo",
        collector_class="DemoCollector",
        entry_param="uid",
        qrcode_selector=".qrcode img",
        publisher_module="mypkg.publishers.demo",
        publisher_class="DemoPublisher",
        supports_text_publish=True,
        publish_url="https://demo.example.com/publish",
    )


def test_validate_spec_passes() -> None:
    assert validate_spec(_full_spec()) == []


def test_validate_spec_flags_missing_entry_param() -> None:
    spec = PlatformCapability(
        platform="demo",
        collector_module="mypkg.collectors.demo",
        collector_class="DemoCollector",
    )
    issues = validate_spec(spec)
    assert any("entry_param" in item for item in issues)


def test_validate_spec_flags_login_without_cookies() -> None:
    spec = PlatformCapability(platform="demo", supports_login=True)
    issues = validate_spec(spec)
    assert any("auth_cookies" in item for item in issues)


def test_validate_spec_flags_bad_selector_type() -> None:
    spec = PlatformCapability(
        platform="demo", qrcode_selector=".qr", qrcode_selector_type="xpath"
    )
    assert any("qrcode_selector_type" in item for item in validate_spec(spec))


def test_derived_view_is_canonical() -> None:
    view = derived_view(_full_spec())
    assert view["auth_cookies"] == ("sessionid", "sid_tt")
    assert view["entry_param"] == ("uid",)
    assert view["publish_url"] == ("https://demo.example.com/publish",)


def test_check_derived_consistency_pass() -> None:
    issues = check_derived_consistency(
        _full_spec(),
        {
            "auth_cookies": {
                "python": ["sessionid", "sid_tt"],
                "javascript": ["sid_tt", "sessionid"],
            },
            "entry_param": ["uid"],
        },
    )
    assert issues == []


def test_check_derived_consistency_detects_drift() -> None:
    issues = check_derived_consistency(
        _full_spec(),
        {"auth_cookies": {"javascript": ["sessionid"]}},
    )
    assert issues and "sid_tt" in issues[0]


def test_registry_rejects_duplicate() -> None:
    registry = PlatformSpecRegistry([_full_spec()])
    with pytest.raises(ValueError, match="重复注册"):
        registry.register(_full_spec())


def test_registry_consistency_and_fingerprint() -> None:
    registry = PlatformSpecRegistry([_full_spec()])
    assert registry.platforms() == ["demo"]
    assert registry.consistency_issues() == []
    first = registry.fingerprint()

    changed = PlatformCapability(
        **{**_full_spec().__dict__, "entry_param": "user_id"}
    )
    registry2 = PlatformSpecRegistry([changed])
    assert registry2.fingerprint() != first


def test_registry_assert_consistent_raises() -> None:
    registry = PlatformSpecRegistry([_full_spec()])
    with pytest.raises(SpecConsistencyError):
        registry.assert_consistent({"demo": {"entry_param": ["user_id"]}})
