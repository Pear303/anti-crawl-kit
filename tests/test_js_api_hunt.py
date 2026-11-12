"""ack-hunt 退出码：0=找到候选，1=没找到，2=运行错误。"""

from __future__ import annotations

from anti_crawl_kit.js_bundle import JsHuntResult
from anti_crawl_kit.tools import js_api_hunt


def test_not_found_returns_1(monkeypatch, tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(
        js_api_hunt,
        "hunt_api",
        lambda **kwargs: JsHuntResult(page_url="https://example.com"),
    )

    code = js_api_hunt.main(
        ["--html", str(page), "--base-url", "https://example.com"]
    )
    assert code == 1


def test_page_fetch_failed_returns_2(monkeypatch):
    """页面抓不到属于运行错误，不能和「没找到候选」混成同一个退出码。"""
    monkeypatch.setattr(js_api_hunt, "fetch_text", lambda *args, **kwargs: (0, "", "boom"))
    monkeypatch.setattr(
        js_api_hunt,
        "hunt_api",
        lambda **kwargs: JsHuntResult(page_url="https://example.com"),
    )

    code = js_api_hunt.main(
        ["--url", "https://example.com", "--browser", "never"]
    )
    assert code == 2
