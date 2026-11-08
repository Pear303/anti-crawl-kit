"""无浏览器测试：平台能力描述 + 登录态三态。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.platform_spec import (
    CHROME_EPOCH_OFFSET,
    CookieState,
    IdentityState,
    PlatformCapability,
    check_identity,
    cookie_expired,
    eval_cookie_state,
    normalize_handle,
    should_block_task,
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
