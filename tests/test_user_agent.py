"""User-Agent 入口的离线测试：重点校验 UA 与 impersonate 版本一致。"""

from __future__ import annotations

from anti_crawl_kit.user_agent import (
    CHROME_MOBILE_UA,
    get_random_user_agent,
    get_ua_for_impersonate,
    get_user_agent,
)


def test_ua_matches_impersonate_version() -> None:
    for name in ("chrome110", "chrome116", "chrome120", "chrome124", "chrome131"):
        ua = get_ua_for_impersonate(name)
        assert f"Chrome/{name.removeprefix('chrome')}.0.0.0" in ua


def test_unknown_impersonate_falls_back_to_default() -> None:
    assert get_ua_for_impersonate("chrome999") == get_ua_for_impersonate()
    assert get_ua_for_impersonate("") == get_ua_for_impersonate()
    assert "Chrome/120" in get_ua_for_impersonate()


def test_get_user_agent_mobile_and_desktop() -> None:
    assert get_user_agent(mobile=True) == CHROME_MOBILE_UA
    assert "Chrome/120" in get_user_agent()


def test_random_user_agent_stays_in_same_generation() -> None:
    allowed = ("Chrome/120", "Chrome/124", "Chrome/131")
    for _ in range(30):
        ua = get_random_user_agent()
        assert any(version in ua for version in allowed)
        assert "HeadlessChrome" not in ua


def test_random_user_agent_mobile() -> None:
    assert get_random_user_agent(mobile=True) == CHROME_MOBILE_UA
