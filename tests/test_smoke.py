"""真实站点冒烟的离线测试（不访问网络，用替身）。"""

from __future__ import annotations

import json
from pathlib import Path

from anti_crawl_kit.smoke import (
    SmokeCase,
    SmokeKind,
    SmokeReport,
    SmokeResult,
    count_in_html,
    run_case,
    run_smoke,
)
from anti_crawl_kit.tools import smoke_cli


class FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class FakeClient:
    def __init__(self, pages: dict[str, str | int]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get_text(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(url)
        value = self.pages.get(url, (404, ""))
        if isinstance(value, tuple):
            status, text = value
            return FakeResponse(status, text)
        return FakeResponse(200, value)


class FakePage:
    def __init__(
        self,
        counts: dict[str, int] | None = None,
        *,
        url_after: str = "https://www.saucedemo.com/inventory.html",
        default_count: int = 0,
    ) -> None:
        self.counts = counts or {}
        self.url_after = url_after
        self.default_count = default_count
        self.filled: list[tuple[str, str]] = []
        self.clicked: list[str] = []
        self.scrolls = 0
        self.last_nav_metrics = {"goto_ms": 1200, "slow_load": False}

    def goto(self, url: str, wait_until: str = "", timeout: int = 0) -> None:
        self.url = url

    def evaluate(self, script: str, arg=None, **kwargs):
        if "querySelectorAll" in script:
            return self.counts.get(str(arg), self.default_count)
        if "scrollTo" in script:
            self.scrolls += 1
            return 1000
        if "location.href" in script:
            return self.url_after
        return None

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def fill(self, selector: str, value: str) -> None:
        self.filled.append((selector, value))

    def click(self, selector: str) -> None:
        self.clicked.append(selector)

    def wait_for_selector(self, selector: str, timeout: int = 0) -> bool:
        return True


def _case(kind: SmokeKind, **overrides) -> SmokeCase:
    data = dict(
        name="case",
        kind=kind,
        url="https://example.com/",
        item_selector="div.item",
        expected_min=10,
    )
    data.update(overrides)
    return SmokeCase(**data)


# ── HTML 计数 ────────────────────────────────────────
def test_count_in_html_tag_class() -> None:
    html = '<article class="product_pod">x</article><article class="other">y</article>'
    assert count_in_html(html, "article.product_pod") == 1
    assert count_in_html(html, ".other") == 1
    assert count_in_html(html, "article") == 2


def test_count_in_html_id_and_attr() -> None:
    html = '<div id="main"></div><a data-test="product-name" href="/product/1"></a>'
    assert count_in_html(html, "#main") == 1
    assert count_in_html(html, "[data-test='product-name']") == 1
    assert count_in_html(html, "a[href*='/product/']") == 1


def test_count_in_html_no_match() -> None:
    assert count_in_html("<div class='quote'></div>", "div.missing") == 0


# ── HTTP 用例 ────────────────────────────────────────
def test_http_static_pass() -> None:
    client = FakeClient({"https://example.com/": '<div class="item"></div>' * 10})
    result = run_case(_case(SmokeKind.HTTP_STATIC), http_client=client)
    assert result.status == "PASS"
    assert result.http_count == 10


def test_http_static_fail_on_short_count() -> None:
    client = FakeClient({"https://example.com/": '<div class="item"></div>' * 3})
    result = run_case(_case(SmokeKind.HTTP_STATIC), http_client=client)
    assert result.status == "FAIL"
    assert "3 条" in result.actual


def test_http_pagination_checks_page2() -> None:
    case = _case(
        SmokeKind.HTTP_PAGINATION,
        page2_url="https://example.com/page2",
        expected_page2_min=10,
    )
    client = FakeClient(
        {
            "https://example.com/": '<div class="item"></div>' * 10,
            "https://example.com/page2": '<div class="item"></div>' * 2,
        }
    )
    result = run_case(case, http_client=client)
    assert result.status == "FAIL"
    assert "第二页" in result.actual
    assert result.metrics["page2"]["count"] == 2


def test_http_pagination_pass() -> None:
    case = _case(
        SmokeKind.HTTP_PAGINATION,
        page2_url="https://example.com/page2",
        expected_page2_min=10,
    )
    client = FakeClient(
        {
            "https://example.com/": '<div class="item"></div>' * 10,
            "https://example.com/page2": '<div class="item"></div>' * 10,
        }
    )
    assert run_case(case, http_client=client).status == "PASS"


# ── JS 渲染 ──────────────────────────────────────────
def test_js_render_pass() -> None:
    case = _case(SmokeKind.JS_RENDER, http_expected_max=0)
    client = FakeClient({"https://example.com/": "<html></html>"})
    page = FakePage(counts={"div.item": 10})
    result = run_case(case, page=page, http_client=client)
    assert result.status == "PASS"
    assert result.http_count == 0
    assert result.browser_count == 10


def test_js_render_fails_if_http_already_has_items() -> None:
    case = _case(SmokeKind.JS_RENDER, http_expected_max=0)
    client = FakeClient({"https://example.com/": '<div class="item"></div>' * 10})
    result = run_case(case, page=FakePage(counts={"div.item": 10}), http_client=client)
    assert result.status == "FAIL"
    assert "SSR" in result.detail


def test_js_render_without_browser_is_skip() -> None:
    case = _case(SmokeKind.JS_RENDER, http_expected_max=0)
    client = FakeClient({"https://example.com/": "<html></html>"})
    assert run_case(case, page=None, http_client=client).status == "SKIP"


# ── 慢加载 / 滚动 / 登录 ─────────────────────────────
def test_slow_load_pass() -> None:
    case = _case(SmokeKind.SLOW_LOAD, render_timeout_ms=1000)
    result = run_case(case, page=FakePage(counts={"div.item": 10}))
    assert result.status == "PASS"


def test_slow_load_fail_when_still_empty() -> None:
    case = _case(SmokeKind.SLOW_LOAD, render_timeout_ms=200)
    result = run_case(case, page=FakePage(counts={"div.item": 0}))
    assert result.status == "FAIL"


def test_infinite_scroll_pass() -> None:
    case = _case(SmokeKind.INFINITE_SCROLL, scroll_rounds=3)
    result = run_case(case, page=FakePage(counts={"div.item": 10}))
    assert result.status == "PASS"
    assert result.browser_count == 10


def test_infinite_scroll_allows_more_than_minimum() -> None:
    """expected_max=None 只要求下限；滚出 100 条不应判失败。"""
    case = _case(SmokeKind.INFINITE_SCROLL, scroll_rounds=3, expected_min=10)
    result = run_case(case, page=FakePage(counts={"div.item": 100}))
    assert result.status == "PASS"
    assert result.browser_count == 100


def test_infinite_scroll_fail_when_empty() -> None:
    case = _case(SmokeKind.INFINITE_SCROLL, scroll_rounds=2)
    result = run_case(case, page=FakePage(counts={"div.item": 0}))
    assert result.status == "FAIL"


def test_login_pass() -> None:
    case = _case(
        SmokeKind.LOGIN,
        login_user="standard_user",
        login_password="secret_sauce",
        login_success_selector=".inventory_item",
        expected_min=6,
    )
    page = FakePage(counts={".inventory_item": 6})
    result = run_case(case, page=page)
    assert result.status == "PASS"
    assert page.filled == [
        ("#user-name", "standard_user"),
        ("#password", "secret_sauce"),
    ]
    assert page.clicked == ["#login-button"]


def test_login_fails_on_wrong_redirect() -> None:
    case = _case(
        SmokeKind.LOGIN,
        login_user="u",
        login_password="p",
        login_success_selector=".inventory_item",
        expected_min=6,
    )
    page = FakePage(
        counts={".inventory_item": 6},
        url_after="https://www.saucedemo.com/",
    )
    assert run_case(case, page=page).status == "FAIL"


# ── 负向控制 ─────────────────────────────────────────
def test_negative_control_passes_when_selector_is_wrong() -> None:
    case = _case(SmokeKind.NEGATIVE_CONTROL, item_selector="div.wrong")
    client = FakeClient({"https://example.com/": '<div class="item"></div>' * 10})
    result = run_case(case, http_client=client)
    assert result.status == "PASS"
    assert result.http_count == 0


def test_negative_control_fails_when_checker_would_be_ok() -> None:
    case = _case(SmokeKind.NEGATIVE_CONTROL, item_selector="div.item")
    client = FakeClient({"https://example.com/": '<div class="item"></div>' * 10})
    result = run_case(case, http_client=client)
    assert result.status == "FAIL"
    assert "负向控制失效" in result.detail


# ── 报告 / CLI ───────────────────────────────────────
def test_run_smoke_report_and_write(tmp_path: Path) -> None:
    client = FakeClient(
        {
            "https://example.com/": '<div class="item"></div>' * 10,
        }
    )
    report = run_smoke([_case(SmokeKind.HTTP_STATIC)], http_client=client)
    assert report.counts()["PASS"] == 1
    assert report.has_fail() is False
    assert "[PASS]" in report.render()
    out = report.write(tmp_path / "smoke.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["PASS"] == 1


def test_report_fail_counts() -> None:
    report = SmokeReport()
    report.add(SmokeResult("a", "http_static", True, "PASS"))
    report.add(SmokeResult("b", "http_static", False, "FAIL"))
    assert report.counts() == {"PASS": 1, "FAIL": 1, "SKIP": 0}
    assert report.has_fail()


def test_cli_merge_attempts() -> None:
    first = SmokeReport()
    first.add(SmokeResult("a", "http_static", True, "PASS", elapsed_ms=10))
    second = SmokeReport()
    second.add(SmokeResult("a", "http_static", False, "FAIL", elapsed_ms=20))
    merged = smoke_cli._merge_attempts([first, second])
    assert merged.results[0].status == "FAIL"
    assert merged.results[0].elapsed_ms == 20
    assert len(merged.results[0].metrics["attempts"]) == 2


def test_cli_only_rejects_unknown() -> None:
    assert smoke_cli.main(["--only", "no-such-case"]) == 2
