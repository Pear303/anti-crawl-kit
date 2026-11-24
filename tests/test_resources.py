"""资源拦截器的离线测试：用 fake page/route，不开浏览器。"""

from __future__ import annotations

from typing import Any

from anti_crawl_kit.resources import (
    DEFAULT_BLOCKED_EXTENSIONS,
    DEFAULT_BLOCKED_RESOURCE_TYPES,
    ResourceBlocker,
    block_unnecessary_resources,
)


class FakeRequest:
    def __init__(self, url: str, resource_type: str = "document") -> None:
        self.url = url
        self.resource_type = resource_type


class FakeRoute:
    def __init__(self, url: str, resource_type: str = "document") -> None:
        self.request = FakeRequest(url, resource_type)
        self.action = ""

    def abort(self) -> None:
        self.action = "abort"

    def continue_(self) -> None:
        self.action = "continue"


class FakePage:
    def __init__(self) -> None:
        self.pattern = ""
        self.handler: Any = None

    def route(self, pattern: str, handler: Any) -> None:
        self.pattern = pattern
        self.handler = handler


def _apply(blocker: ResourceBlocker, url: str, resource_type: str = "document") -> str:
    page = FakePage()
    blocker.apply(page)
    assert page.pattern == "**/*"
    route = FakeRoute(url, resource_type)
    page.handler(route)
    return route.action


def test_defaults_cover_media_types() -> None:
    assert "image" in DEFAULT_BLOCKED_RESOURCE_TYPES
    assert "font" in DEFAULT_BLOCKED_RESOURCE_TYPES
    assert ".png" in DEFAULT_BLOCKED_EXTENSIONS
    assert ".mp4" in DEFAULT_BLOCKED_EXTENSIONS
    # 验证码/二维码通常也是图片，所以默认不推荐开启，这里只是记录事实
    assert ".svg" in DEFAULT_BLOCKED_EXTENSIONS


def test_aborts_by_resource_type() -> None:
    blocker = ResourceBlocker()
    assert _apply(blocker, "https://example.com/a", "image") == "abort"
    assert _apply(blocker, "https://example.com/a", "media") == "abort"
    assert _apply(blocker, "https://example.com/a", "document") == "continue"


def test_aborts_by_extension_ignoring_query() -> None:
    blocker = ResourceBlocker()
    assert _apply(blocker, "https://cdn.example.com/x.PNG?token=1") == "abort"
    assert _apply(blocker, "https://cdn.example.com/file.mp4?x=1") == "abort"
    assert _apply(blocker, "https://cdn.example.com/file.json") == "continue"


def test_whitelist_domains_bypass_everything() -> None:
    blocker = ResourceBlocker(whitelist_domains=["qrcode.example.com"])
    assert _apply(blocker, "https://qrcode.example.com/code.png", "image") == "continue"
    assert _apply(blocker, "https://other.example.com/code.png", "image") == "abort"


def test_custom_sets_replace_defaults() -> None:
    blocker = ResourceBlocker(resource_types=["stylesheet"], extensions=[".tmp"])
    assert _apply(blocker, "https://example.com/a", "image") == "continue"
    assert _apply(blocker, "https://example.com/a", "stylesheet") == "abort"
    assert _apply(blocker, "https://example.com/a.tmp") == "abort"


def test_apply_to_page_registers_route_and_returns_blocker() -> None:
    page = FakePage()
    blocker = ResourceBlocker.apply_to_page(page, whitelist_domains=["qr.example.com"])
    assert isinstance(blocker, ResourceBlocker)
    assert blocker.whitelist_domains == ("qr.example.com",)
    assert page.pattern == "**/*"


def test_block_unnecessary_resources_is_alias() -> None:
    page = FakePage()
    blocker = block_unnecessary_resources(page)
    assert isinstance(blocker, ResourceBlocker)
    assert page.handler is not None
