"""浏览器会话池测试（离线，用替身 browser）。"""

from __future__ import annotations

import time

import pytest

from anti_crawl_kit.browser_pool import BrowserPool, _ThreadSession


class FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, args: dict) -> None:
        self.args = args
        self.init_scripts: list[str] = []
        self.page = FakePage()
        self.closed = False

    def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.connected = True
        self.contexts: list[FakeContext] = []

    def new_context(self, **kwargs) -> FakeContext:
        context = FakeContext(kwargs)
        self.contexts.append(context)
        return context

    def is_connected(self) -> bool:
        return self.connected

    def close(self) -> None:
        self.connected = False


class FakePlaywright:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _factory(sessions: list[_ThreadSession]):
    def make() -> _ThreadSession:
        now = time.time()
        session = _ThreadSession(
            playwright=FakePlaywright(),
            browser=FakeBrowser(),
            created_at=now,
            last_used=now,
        )
        sessions.append(session)
        return session

    return make


def test_reuses_browser_within_thread() -> None:
    sessions: list[_ThreadSession] = []
    pool = BrowserPool(browser_factory=_factory(sessions), stealth=False)
    with pool.lease() as page:
        assert page.raw is sessions[0].browser.contexts[0].page
    with pool.lease() as page:
        assert page.raw is sessions[0].browser.contexts[1].page

    assert len(sessions) == 1
    stats = pool.stats()
    assert stats["sessions_created"] == 1
    assert stats["sessions_reused"] == 1
    assert stats["contexts_created"] == 2
    assert stats["leases"] == 2


def test_context_is_isolated_and_closed_per_lease() -> None:
    sessions: list[_ThreadSession] = []
    pool = BrowserPool(browser_factory=_factory(sessions), stealth=False)
    with pool.lease():
        first = sessions[0].browser.contexts[0]
    assert first.closed is True

    with pool.lease():
        second = sessions[0].browser.contexts[1]
    assert second is not first
    assert first is not second


def test_rebuilds_when_browser_disconnected() -> None:
    sessions: list[_ThreadSession] = []
    pool = BrowserPool(browser_factory=_factory(sessions), stealth=False)
    with pool.lease():
        pass
    sessions[0].browser.connected = False
    with pool.lease():
        pass
    assert len(sessions) == 2
    assert pool.stats()["sessions_created"] == 2


def test_rebuilds_after_idle_ttl() -> None:
    sessions: list[_ThreadSession] = []
    pool = BrowserPool(
        browser_factory=_factory(sessions), stealth=False, idle_ttl=0.001
    )
    with pool.lease():
        pass
    time.sleep(0.01)
    with pool.lease():
        pass
    assert len(sessions) == 2


def test_closed_pool_raises() -> None:
    pool = BrowserPool(browser_factory=_factory([]), stealth=False)
    pool.shutdown()
    with pytest.raises(RuntimeError, match="已关闭"):
        with pool.lease():
            pass


def test_context_args_headless() -> None:
    pool = BrowserPool(browser_factory=_factory([]), stealth=False, headless=True)
    args = pool._context_args()
    assert args["viewport"] == {"width": 1920, "height": 1080}
    assert args["device_scale_factor"] == 1


def test_context_args_headed() -> None:
    pool = BrowserPool(browser_factory=_factory([]), stealth=False, headless=False)
    assert pool._context_args()["viewport"] is None


def test_stats_contains_config() -> None:
    pool = BrowserPool(browser_factory=_factory([]), max_sessions=3, stealth=False)
    stats = pool.stats()
    assert stats["max_sessions"] == 3
    assert stats["headless"] is True
