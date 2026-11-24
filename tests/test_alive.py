"""浏览器存活探测的离线测试：用 fake page 模拟各种异常路径。"""

from __future__ import annotations

from typing import Any

import pytest

from anti_crawl_kit.alive import (
    BROWSER_CLOSED_MESSAGE,
    BrowserClosedError,
    ensure_alive,
    is_page_closed,
)


class Page:
    def __init__(self, *, closed: bool = False, evaluate_error: str = "") -> None:
        self.closed = closed
        self.evaluate_error = evaluate_error
        self.evaluated = 0

    def is_closed(self) -> bool:
        return self.closed

    def evaluate(self, script: str) -> Any:
        self.evaluated += 1
        if self.evaluate_error:
            raise RuntimeError(self.evaluate_error)
        return 1


class ClosedOnQuery(Page):
    def is_closed(self) -> bool:
        raise RuntimeError("Target page, context or browser has been closed")


class NoIsClosed:
    def __init__(self, evaluate_error: str = "") -> None:
        self.evaluate_error = evaluate_error

    def evaluate(self, script: str) -> Any:
        if self.evaluate_error:
            raise RuntimeError(self.evaluate_error)
        return 1


def test_healthy_page_is_alive() -> None:
    page = Page()
    assert is_page_closed(page) is False
    ensure_alive(page)  # 不抛异常


def test_is_closed_fast_path_wins_without_evaluate() -> None:
    page = Page(closed=True)
    assert is_page_closed(page) is True
    assert page.evaluated == 0


def test_is_closed_error_classified_by_message() -> None:
    assert is_page_closed(ClosedOnQuery()) is True

    class Boom(Page):
        def is_closed(self) -> bool:
            raise RuntimeError("timeout 30000ms exceeded")

    assert is_page_closed(Boom()) is False


def test_evaluate_closed_message_means_closed() -> None:
    page = Page(evaluate_error="Target page, context or browser has been closed")
    assert is_page_closed(page) is True


def test_evaluate_other_error_is_conservatively_alive() -> None:
    # 导航期间会抛 Execution context was destroyed，窗口其实还活着
    page = Page(evaluate_error="Execution context was destroyed, most likely because of a navigation")
    assert is_page_closed(page) is False


def test_page_without_is_closed_still_probed() -> None:
    assert is_page_closed(NoIsClosed()) is False
    assert is_page_closed(NoIsClosed("Target closed")) is True


def test_ensure_alive_raises_with_label() -> None:
    with pytest.raises(BrowserClosedError) as excinfo:
        ensure_alive(Page(closed=True), label="登录等待")

    message = str(excinfo.value)
    assert BROWSER_CLOSED_MESSAGE in message
    assert "[登录等待]" in message
    # 保持 RuntimeError 兼容，现有 except Exception 不受影响
    assert isinstance(excinfo.value, RuntimeError)
