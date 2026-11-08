"""PageAdapter 首导航自愈 + 慢加载语义测试（用替身 driver，不启动浏览器）。"""

from __future__ import annotations

import pytest

from anti_crawl_kit.page import PageAdapter


class FakeDriver:
    def __init__(
        self,
        *,
        goto_exc: Exception | None = None,
        reload_exc: Exception | None = None,
        ready_state: str = "complete",
        url: str = "https://example.com",
        closed: bool = False,
        load_state_exc: Exception | None = None,
    ) -> None:
        self.goto_exc = goto_exc
        self.reload_exc = reload_exc
        self.ready_state = ready_state
        self.url = url
        self.closed = closed
        self.load_state_exc = load_state_exc
        self.goto_calls = 0
        self.reload_calls = 0

    def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None):
        self.goto_calls += 1
        if self.goto_exc:
            raise self.goto_exc
        self.url = url
        return {"status": 200, "url": url}

    def reload(self, wait_until: str | None = None, timeout: int | None = None):
        self.reload_calls += 1
        if self.reload_exc:
            raise self.reload_exc
        return {"status": 200, "url": self.url}

    def evaluate(self, script: str):
        if "readyState" in script:
            return self.ready_state
        return ""

    def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
        if self.load_state_exc:
            raise self.load_state_exc

    def is_closed(self) -> bool:
        return self.closed

    def wait_for_timeout(self, ms: int) -> None:
        return None


def _adapter(driver: FakeDriver) -> PageAdapter:
    return PageAdapter(driver, anti_crawl=False)


def test_goto_returns_response_and_metrics() -> None:
    driver = FakeDriver()
    adapter = _adapter(driver)
    response = adapter.goto("https://example.com/a")
    assert response["status"] == 200
    metrics = adapter.last_nav_metrics
    assert metrics["url"] == "https://example.com/a"
    assert metrics["attempts"] == 1


def test_ready_state_does_not_pass_timeout_kwarg() -> None:
    """patchright 1.62.1 的 sync Page.evaluate 没有 timeout 参数。"""
    driver = FakeDriver(ready_state="interactive")
    adapter = _adapter(driver)
    assert adapter._ready_state() == "interactive"


def test_timeout_recovered_when_page_is_interactive() -> None:
    driver = FakeDriver(
        goto_exc=TimeoutError("Timeout 20000ms exceeded"),
        ready_state="complete",
    )
    adapter = _adapter(driver)
    response = adapter.goto("https://example.com/slow", max_retries=1)
    assert response is None
    assert adapter.last_nav_metrics["recovered_from_timeout"] is True
    assert adapter.last_nav_metrics["slow_load"] is True


def test_timeout_raises_with_wait_hint_when_not_ready() -> None:
    driver = FakeDriver(
        goto_exc=TimeoutError("Timeout 20000ms exceeded"),
        ready_state="loading",
        load_state_exc=Exception("timeout"),
    )
    adapter = _adapter(driver)
    with pytest.raises(TimeoutError) as excinfo:
        adapter.goto("https://example.com/slow", max_retries=1)
    assert "等待 1~2 分钟" in str(excinfo.value)
    assert adapter.last_nav_metrics["error"]


def test_chrome_error_treated_as_interactive() -> None:
    driver = FakeDriver(
        goto_exc=TimeoutError("Timeout"),
        ready_state="loading",
        url="chrome-error://chromewebdata/",
    )
    adapter = _adapter(driver)
    assert adapter.goto("https://example.com/x", max_retries=1) is None
    assert adapter.last_nav_metrics["recovered_from_timeout"] is True


def test_about_blank_skips_ready_check() -> None:
    driver = FakeDriver(ready_state="loading")
    adapter = _adapter(driver)
    response = adapter.goto("about:blank")
    assert response["status"] == 200
    assert adapter.last_nav_metrics["slow_load"] is False


def test_slow_load_flag_when_over_threshold() -> None:
    driver = FakeDriver()
    adapter = _adapter(driver)
    adapter.goto("https://example.com/x", slow_load_ms=0)
    assert adapter.last_nav_metrics["slow_load"] is True


def test_non_timeout_error_is_reraised() -> None:
    driver = FakeDriver(goto_exc=ValueError("boom"), reload_exc=ValueError("boom"))
    adapter = _adapter(driver)
    with pytest.raises(ValueError, match="boom"):
        adapter.goto("https://example.com/x", max_retries=2, backoff_base=0)


def test_closed_page_fails_ready_check() -> None:
    driver = FakeDriver(closed=True)
    assert _adapter(driver)._ensure_initial_navigation_ready() is False
