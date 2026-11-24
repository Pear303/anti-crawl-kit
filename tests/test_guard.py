"""反爬守卫的离线测试：域名归一、延时调用、风控页判定。"""

from __future__ import annotations

from typing import Any

from anti_crawl_kit import guard as guard_module
from anti_crawl_kit.guard import AntiCrawlGuard, get_global_guard


def test_domain_of_normalizes_variants() -> None:
    domain_of = AntiCrawlGuard._domain_of
    assert domain_of("https://WWW.Example.com/a?b=1") == "www.example.com"
    assert domain_of("http://user:pw@x.test/path") == "x.test"
    assert domain_of("about:blank") == "__none__"
    assert domain_of("") == "__none__"
    assert domain_of("not-a-url") == "not-a-url"


def test_nav_gap_first_visit_and_repeat(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(guard_module.time, "sleep", sleeps.append)

    guard = AntiCrawlGuard(isolate=True, min_interval=1.0, max_interval=1.0)
    guard.wait_nav_gap("https://a.test/1")
    assert len(sleeps) == 1
    assert 0.2 <= sleeps[0] <= 0.8

    sleeps.clear()
    guard.wait_nav_gap("https://a.test/2")
    assert len(sleeps) == 1
    assert 0.0 <= sleeps[0] <= 1.0


def test_nav_gap_disabled_does_not_sleep(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(guard_module.time, "sleep", sleeps.append)

    guard = AntiCrawlGuard(enable_delay=False)
    guard.wait_nav_gap("https://a.test/")
    guard.wait_backoff("captcha")
    assert sleeps == []


def test_backoff_grows_and_caps(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(guard_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(guard_module.random, "uniform", lambda a, b: 1.0)

    guard = AntiCrawlGuard()
    for attempt in range(5):
        guard.wait_backoff("captcha", attempt)

    assert sleeps[:3] == [3.0, 6.0, 12.0]
    assert sleeps[3] == 15.0  # 上限
    assert sleeps[4] == 15.0


def test_detect_risk_page_by_url() -> None:
    guard = AntiCrawlGuard()

    captcha = guard.detect_risk_page(None, "https://x.test/captcha")
    assert captcha["detected"] is True
    assert captcha["kind"] == "captcha"

    login = guard.detect_risk_page(None, "https://x.test/请登录")
    assert login["detected"] is True
    assert login["kind"] == "blocked"


def test_detect_risk_page_disabled() -> None:
    guard = AntiCrawlGuard(enable_detect=False)
    assert guard.detect_risk_page(None, "https://x.test/captcha") == {
        "detected": False,
        "kind": "ok",
        "reason": "detect_disabled",
    }


class EvalPage:
    def __init__(self, result: Any) -> None:
        self.result = result

    def evaluate(self, script: str) -> Any:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_detect_risk_via_dom_paths() -> None:
    guard = AntiCrawlGuard()

    hit = guard.detect_risk_page(
        EvalPage({"detected": True, "kind": "captcha", "reason": "dom_captcha"}),
        "https://x.test/list",
    )
    assert hit == {"detected": True, "kind": "captcha", "reason": "dom_captcha"}

    miss = guard.detect_risk_page(EvalPage({"detected": False}), "https://x.test/list")
    assert miss["detected"] is False

    non_dict = guard.detect_risk_page(EvalPage("weird"), "https://x.test/list")
    assert non_dict == {"detected": False, "kind": "ok", "reason": ""}

    broken = guard.detect_risk_page(EvalPage(RuntimeError("boom")), "https://x.test/list")
    assert broken["detected"] is False
    assert broken["reason"].startswith("dom_error:")


def test_detect_risk_page_without_evaluate() -> None:
    guard = AntiCrawlGuard()
    result = guard.detect_risk_page(object(), "https://x.test/list")
    assert result["reason"] == "no_evaluate"


def test_get_global_guard_is_singleton() -> None:
    assert get_global_guard() is get_global_guard()
