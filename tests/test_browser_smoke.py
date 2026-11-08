"""浏览器集成冒烟测试（默认跳过）。

需要真实 Chrome + patchright + 外网，因此不放进默认 CI。

运行::

    # PowerShell
    $env:ACK_BROWSER = "1"; python -m pytest tests -m browser -q

    # bash
    ACK_BROWSER=1 python -m pytest tests -m browser -q
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.getenv("ACK_BROWSER") != "1",
        reason="设置 ACK_BROWSER=1 才跑浏览器集成测试",
    ),
]


def test_launch_and_navigate() -> None:
    pytest.importorskip("patchright", reason="需要 [browser] extra")
    from anti_crawl_kit import BrowserSession

    with BrowserSession(headless=True, skip_version_check=True) as page:
        page.goto("https://quotes.toscrape.com/", timeout=30000)
        assert "Quotes" in page.title()


def test_http_client_against_static_site() -> None:
    pytest.importorskip("curl_cffi", reason="需要 [http] extra")
    from anti_crawl_kit import AntiRiskHttpClient

    client = AntiRiskHttpClient("smoke")
    result = client.get_text("https://quotes.toscrape.com/")
    assert result.status_code == 200
    assert "quote" in result.text.lower()
