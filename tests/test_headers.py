"""headers 工厂与 Referer 推断的离线测试。"""

from __future__ import annotations

from anti_crawl_kit.headers import get_platform_headers, get_referer_for_url


def test_platform_headers_have_referer_for_known_platform() -> None:
    headers = get_platform_headers("bilibili")
    assert headers["Referer"] == "https://www.bilibili.com/"
    assert headers["Accept-Language"] == "zh-CN,zh;q=0.9"
    assert headers["User-Agent"]


def test_platform_headers_accept_modes() -> None:
    as_json = get_platform_headers("zhihu")
    assert as_json["Accept"].startswith("application/json")

    as_html = get_platform_headers("zhihu", accept="html")
    assert as_html["Accept"].startswith("text/html")


def test_platform_headers_unknown_platform_has_no_referer() -> None:
    headers = get_platform_headers("no-such-platform")
    assert "Referer" not in headers


def test_platform_headers_extra_overrides_default() -> None:
    headers = get_platform_headers("weibo", extra={"Referer": "https://x.test/", "X-Token": "t"})
    assert headers["Referer"] == "https://x.test/"
    assert headers["X-Token"] == "t"


def test_referer_for_url_matches_cdn_keywords() -> None:
    assert get_referer_for_url("https://upos-sz.bilivideo.com/a.m4s") == "https://www.bilibili.com/"
    assert get_referer_for_url("https://v.kuaishou.com/x") == "https://www.kuaishou.com/"
    assert get_referer_for_url("https://v3-web.douyin.com/x") == "https://www.douyin.com/"


def test_referer_for_url_returns_none() -> None:
    assert get_referer_for_url("") is None
    assert get_referer_for_url("https://example.com/") is None
    assert get_referer_for_url("file:///tmp/a.txt") is None
