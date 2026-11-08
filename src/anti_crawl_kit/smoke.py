"""真实站点冒烟 —— 发行门槛，mock 不算验证。

为什么需要它
------------
patchright 大切换那次只跑了 mock + 检测站 + 百度，各平台真实 DOM/接口链路
没实测，复盘时被列为**最大风险项**。这个模块把「真实站点跑完整链路」固化成
命令，覆盖 8 类故障：

    http_static       纯 HTTP 静态列表
    http_pagination   纯 HTTP 分页（第二页也必须能拿）
    js_render         HTTP 是空壳，浏览器才拿得到
    big_list          大列表覆盖率（expected == actual）
    slow_load         慢渲染不判失败
    infinite_scroll   无限滚动 + 停止条件
    login             登录链路（公开测试账号）
    negative_control  故意改错选择器，必须判失败

设计原则：
    - 默认用例全部是公开练习站（允许抓），不碰真实平台；
    - 只读：不发布、不写平台数据；
    - 每条用例都带「为什么失败」，输出 JSON + 退出码，可接 CI；
    - 负向控制是核心：如果 0 条也能判 OK，说明检测层本身坏了。

用法::

    ack-smoke                          # 全部用例（需要浏览器）
    ack-smoke --no-browser             # 只跑纯 HTTP 用例，浏览器用例 SKIP
    ack-smoke --only books_pagination,quotes_js_render
    ack-smoke --json smoke.json
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


class SmokeKind(StrEnum):
    HTTP_STATIC = "http_static"
    HTTP_PAGINATION = "http_pagination"
    JS_RENDER = "js_render"
    BIG_LIST = "big_list"
    SLOW_LOAD = "slow_load"
    INFINITE_SCROLL = "infinite_scroll"
    LOGIN = "login"
    NEGATIVE_CONTROL = "negative_control"


_BROWSER_KINDS = frozenset(
    {
        SmokeKind.JS_RENDER,
        SmokeKind.SLOW_LOAD,
        SmokeKind.INFINITE_SCROLL,
        SmokeKind.LOGIN,
    }
)


@dataclass(frozen=True)
class SmokeCase:
    """一条冒烟用例。"""

    name: str
    kind: SmokeKind
    url: str
    item_selector: str
    expected_min: int
    expected_max: int | None = None

    # 分页
    page2_url: str = ""
    expected_page2_min: int = 0

    # JS 渲染：HTTP 侧必须是空壳
    http_expected_max: int | None = None

    # 慢加载 / 滚动
    render_timeout_ms: int = 15000
    min_elapsed_ms: int = 0
    scroll_rounds: int = 6

    # 登录
    login_user: str = ""
    login_password: str = ""
    login_user_selector: str = "#user-name"
    login_password_selector: str = "#password"
    login_submit_selector: str = "#login-button"
    login_success_selector: str = ""

    notes: str = ""

    @property
    def requires_browser(self) -> bool:
        return self.kind in _BROWSER_KINDS


@dataclass
class SmokeResult:
    name: str
    kind: str
    ok: bool
    status: str                     # PASS / FAIL / SKIP
    expected: str = ""
    actual: str = ""
    http_count: int | None = None
    browser_count: int | None = None
    elapsed_ms: int = 0
    detail: str = ""
    error: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "ok": self.ok,
            "status": self.status,
            "expected": self.expected,
            "actual": self.actual,
            "http_count": self.http_count,
            "browser_count": self.browser_count,
            "elapsed_ms": self.elapsed_ms,
            "detail": self.detail,
            "error": self.error,
            "metrics": self.metrics,
        }


@dataclass
class SmokeReport:
    started_at: str = ""
    finished_at: str = ""
    results: list[SmokeResult] = field(default_factory=list)

    def add(self, result: SmokeResult) -> None:
        self.results.append(result)

    def counts(self) -> dict[str, int]:
        out = {"PASS": 0, "FAIL": 0, "SKIP": 0}
        for result in self.results:
            out[result.status] = out.get(result.status, 0) + 1
        return out

    def has_fail(self) -> bool:
        return any(result.status == "FAIL" for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.counts(),
            "results": [result.to_dict() for result in self.results],
        }

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def render(self) -> str:
        counts = self.counts()
        lines = [
            "=" * 78,
            f"  真实站点冒烟  {self.started_at} -> {self.finished_at}",
            (
                f"  汇总: PASS={counts.get('PASS', 0)}  FAIL={counts.get('FAIL', 0)}  "
                f"SKIP={counts.get('SKIP', 0)}"
            ),
            "=" * 78,
        ]
        marks = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}
        for result in self.results:
            lines.append(
                f"  {marks.get(result.status, result.status)} "
                f"{result.name:<24} {result.actual or result.detail}"
            )
            if result.error:
                lines.append(f"         错误: {result.error}")
        lines.append("")
        if self.has_fail():
            lines.append("[结论] 存在 FAIL —— 不要发版，先修真实链路")
        else:
            lines.append("[结论] 无 FAIL —— 真实站点链路可用")
        return "\n".join(lines)


# ── HTML 选择器计数（零依赖，够用即可）──────────────

_TOKEN_RE = re.compile(r"^[a-zA-Z][\w-]*|[.#][\w-]+|\[[^\]]+\]")


class _TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        self.tags.append((tag.lower(), {k.lower(): (v or "") for k, v in attrs}))


def _selector_tokens(selector: str) -> list[str]:
    return _TOKEN_RE.findall(selector.strip())


def _match_tag(
    tag: str,
    attrs: dict[str, str],
    tokens: list[str],
) -> bool:
    for token in tokens:
        if token.startswith("."):
            if token[1:] not in attrs.get("class", "").split():
                return False
        elif token.startswith("#"):
            if attrs.get("id") != token[1:]:
                return False
        elif token.startswith("["):
            match = re.fullmatch(r"\[([\w-]+)([*^$]?)=['\"]?(.*?)['\"]?\]", token)
            if not match:
                return False
            name, op, value = match.group(1), match.group(2), match.group(3)
            actual = attrs.get(name, "")
            if op == "*" and value not in actual:
                return False
            if op == "^" and not actual.startswith(value):
                return False
            if op == "$" and not actual.endswith(value):
                return False
            if not op and actual != value:
                return False
        else:
            if tag != token.lower():
                return False
    return True


def count_in_html(html: str, selector: str) -> int:
    """统计原始 HTML 中匹配选择器的元素数（支持 tag / .class / #id / [attr]）。"""
    tokens = _selector_tokens(selector)
    if not tokens:
        return 0
    collector = _TagCollector()
    collector.feed(html or "")
    return sum(1 for tag, attrs in collector.tags if _match_tag(tag, attrs, tokens))


# ── 浏览器辅助 ─────────────────────────────────────

_COUNT_JS = "(sel) => document.querySelectorAll(sel).length"
_SCROLL_JS = "() => { window.scrollTo(0, document.body.scrollHeight); return document.body.scrollHeight; }"
_URL_JS = "() => location.href"


def _browser_count(page: Any, selector: str) -> int:
    try:
        return int(page.evaluate(_COUNT_JS, selector) or 0)
    except Exception:
        return 0


def _wait_for_count(
    page: Any,
    selector: str,
    *,
    minimum: int,
    timeout_ms: int,
    poll_ms: int = 400,
) -> tuple[int, int]:
    """轮询直到命中数 >= minimum 或超时；返回 (count, waited_ms)。"""
    started = time.perf_counter()
    deadline = started + max(100, timeout_ms) / 1000
    count = _browser_count(page, selector)
    while count < minimum and time.perf_counter() < deadline:
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            time.sleep(poll_ms / 1000)
        count = _browser_count(page, selector)
    return count, int((time.perf_counter() - started) * 1000)


def _judge(case: SmokeCase, count: int) -> tuple[bool, str]:
    """判定条目数。

    ``expected_max is None`` 表示只要求下限（例如无限滚动，滚多少都算对）；
    显式给 ``expected_max`` 才做区间/精确判定。
    """
    if case.expected_max is None:
        ok = count >= case.expected_min
        expected = f">= {case.expected_min}"
    else:
        ok = case.expected_min <= count <= case.expected_max
        expected = (
            f"{case.expected_min}"
            if case.expected_max == case.expected_min
            else f"{case.expected_min}..{case.expected_max}"
        )
    return ok, f"{count} 条（期望 {expected}）"


def _http_get(http_client: Any, url: str) -> tuple[int, str]:
    result = http_client.get_text(url)
    return int(result.status_code), result.text


def _run_http(case: SmokeCase, http_client: Any) -> SmokeResult:
    started = time.perf_counter()
    try:
        status, html = _http_get(http_client, case.url)
    except Exception as exc:
        return SmokeResult(
            case.name,
            str(case.kind),
            False,
            "FAIL",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
    if status != 200:
        return SmokeResult(
            case.name,
            str(case.kind),
            False,
            "FAIL",
            actual=f"HTTP {status}",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            detail="非 200",
        )
    count = count_in_html(html, case.item_selector)
    ok, actual = _judge(case, count)
    result = SmokeResult(
        case.name,
        str(case.kind),
        ok,
        "PASS" if ok else "FAIL",
        actual=actual,
        http_count=count,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )

    if case.kind is SmokeKind.HTTP_PAGINATION and case.page2_url:
        try:
            status2, html2 = _http_get(http_client, case.page2_url)
            count2 = count_in_html(html2, case.item_selector) if status2 == 200 else 0
        except Exception as exc:
            status2, count2 = 0, 0
            result.error = f"第二页: {type(exc).__name__}: {str(exc)[:160]}"
        page2_ok = status2 == 200 and count2 >= case.expected_page2_min
        result.metrics["page2"] = {
            "status": status2,
            "count": count2,
            "min": case.expected_page2_min,
        }
        result.ok = result.ok and page2_ok
        result.status = "PASS" if result.ok else "FAIL"
        result.actual = f"{actual}; 第二页 {count2} 条"
        if not page2_ok and not result.error:
            result.error = f"第二页不达标（HTTP {status2}，{count2} 条）"
    return result


def _run_js_render(case: SmokeCase, http_client: Any, page: Any) -> SmokeResult:
    started = time.perf_counter()
    try:
        status, html = _http_get(http_client, case.url)
    except Exception as exc:
        return SmokeResult(
            case.name,
            str(case.kind),
            False,
            "FAIL",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"HTTP: {type(exc).__name__}: {str(exc)[:160]}",
        )
    http_count = count_in_html(html, case.item_selector)
    http_empty_ok = (
        status == 200
        and case.http_expected_max is not None
        and http_count <= case.http_expected_max
    )

    if not http_empty_ok:
        return SmokeResult(
            case.name,
            str(case.kind),
            False,
            "FAIL",
            expected=f"HTTP 原始 HTML <= {case.http_expected_max} 条",
            actual=f"HTTP {http_count} 条",
            http_count=http_count,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            detail="站点可能已改成 SSR；用例需要更新，不能假装 JS 渲染",
        )

    try:
        page.goto(case.url, wait_until="domcontentloaded", timeout=30000)
        count, waited_ms = _wait_for_count(
            page,
            case.item_selector,
            minimum=case.expected_min,
            timeout_ms=case.render_timeout_ms,
        )
    except Exception as exc:
        return SmokeResult(
            case.name,
            str(case.kind),
            False,
            "FAIL",
            http_count=http_count,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"浏览器: {type(exc).__name__}: {str(exc)[:160]}",
        )

    ok, actual = _judge(case, count)
    metrics = {"render_wait_ms": waited_ms}
    if hasattr(page, "last_nav_metrics"):
        metrics["nav"] = dict(getattr(page, "last_nav_metrics") or {})
    return SmokeResult(
        case.name,
        str(case.kind),
        ok,
        "PASS" if ok else "FAIL",
        expected="HTTP 空壳 + 浏览器 10 条",
        actual=f"HTTP {http_count} 条 / 浏览器 {actual}",
        http_count=http_count,
        browser_count=count,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        metrics=metrics,
    )


def _run_slow_load(case: SmokeCase, page: Any) -> SmokeResult:
    started = time.perf_counter()
    try:
        page.goto(case.url, wait_until="domcontentloaded", timeout=30000)
        count, waited_ms = _wait_for_count(
            page,
            case.item_selector,
            minimum=case.expected_min,
            timeout_ms=case.render_timeout_ms,
        )
    except Exception as exc:
        return SmokeResult(
            case.name,
            str(case.kind),
            False,
            "FAIL",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
    ok, actual = _judge(case, count)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if case.min_elapsed_ms and elapsed_ms < case.min_elapsed_ms:
        ok = False
        actual += f"；耗时 {elapsed_ms}ms < 预期慢加载 {case.min_elapsed_ms}ms（用例需更新）"
    metrics = {"render_wait_ms": waited_ms}
    if hasattr(page, "last_nav_metrics"):
        metrics["nav"] = dict(getattr(page, "last_nav_metrics") or {})
    return SmokeResult(
        case.name,
        str(case.kind),
        ok,
        "PASS" if ok else "FAIL",
        expected=f"慢渲染后仍拿到 {case.expected_min} 条",
        actual=actual,
        browser_count=count,
        elapsed_ms=elapsed_ms,
        metrics=metrics,
    )


def _run_infinite_scroll(case: SmokeCase, page: Any) -> SmokeResult:
    started = time.perf_counter()
    try:
        page.goto(case.url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        counts = [_browser_count(page, case.item_selector)]
        stable = 0
        for _ in range(max(1, case.scroll_rounds)):
            try:
                page.evaluate(_SCROLL_JS)
            except Exception:
                break
            page.wait_for_timeout(900)
            current = _browser_count(page, case.item_selector)
            counts.append(current)
            if current == counts[-2]:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
    except Exception as exc:
        return SmokeResult(
            case.name,
            str(case.kind),
            False,
            "FAIL",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )

    final = counts[-1] if counts else 0
    ok, actual = _judge(case, final)
    if final <= 0:
        ok = False
    metrics = {"counts": counts, "rounds": len(counts) - 1, "stopped": stable >= 2}
    return SmokeResult(
        case.name,
        str(case.kind),
        ok,
        "PASS" if ok else "FAIL",
        expected=f"滚动后 >= {case.expected_min} 条且能停止",
        actual=f"{actual}；滚动序列 {counts}",
        browser_count=final,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        metrics=metrics,
    )


def _run_login(case: SmokeCase, page: Any) -> SmokeResult:
    started = time.perf_counter()
    try:
        page.goto(case.url, wait_until="domcontentloaded", timeout=30000)
        page.fill(case.login_user_selector, case.login_user)
        page.fill(case.login_password_selector, case.login_password)
        page.click(case.login_submit_selector)
        count, waited_ms = _wait_for_count(
            page,
            case.login_success_selector,
            minimum=case.expected_min,
            timeout_ms=case.render_timeout_ms,
        )
        url_after = str(page.evaluate(_URL_JS) or "")
    except Exception as exc:
        return SmokeResult(
            case.name,
            str(case.kind),
            False,
            "FAIL",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
    ok, actual = _judge(case, count)
    if "inventory" not in url_after:
        ok = False
        actual += f"；登录后 URL 异常: {url_after}"
    return SmokeResult(
        case.name,
        str(case.kind),
        ok,
        "PASS" if ok else "FAIL",
        expected=f"登录后 >= {case.expected_min} 件商品",
        actual=actual,
        browser_count=count,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        metrics={"url_after": url_after, "render_wait_ms": waited_ms},
    )


def _run_negative_control(case: SmokeCase, http_client: Any) -> SmokeResult:
    started = time.perf_counter()
    try:
        status, html = _http_get(http_client, case.url)
    except Exception as exc:
        # 负向控制连请求都失败，说明不到「检测层是否失效」，判 FAIL
        return SmokeResult(
            case.name,
            str(case.kind),
            False,
            "FAIL",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {str(exc)[:160]}",
        )
    count = count_in_html(html, case.item_selector) if status == 200 else 0
    # 负向控制：用错误选择器必须拿到 0 条，从而被 _judge 判失败。
    observed_ok, _ = _judge(case, count)
    ok = status == 200 and not observed_ok
    return SmokeResult(
        case.name,
        str(case.kind),
        ok,
        "PASS" if ok else "FAIL",
        expected="错误选择器必须判失败（不能默认 OK）",
        actual=f"错误选择器命中 {count} 条，检测层判 {'OK' if observed_ok else 'FAIL'}",
        http_count=count,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        detail="负向控制生效" if ok else "负向控制失效：检测层把 0 条当成功了",
    )


def run_case(
    case: SmokeCase,
    *,
    page: Any = None,
    http_client: Any = None,
) -> SmokeResult:
    """跑单条用例；浏览器缺失时浏览器类用例返回 SKIP。"""
    if case.requires_browser and page is None:
        return SmokeResult(
            case.name,
            str(case.kind),
            False,
            "SKIP",
            detail="未提供浏览器（--no-browser 或 patchright 不可用）",
        )

    if case.kind is SmokeKind.NEGATIVE_CONTROL:
        return _run_negative_control(case, http_client)
    if case.kind is SmokeKind.JS_RENDER:
        return _run_js_render(case, http_client, page)
    if case.kind is SmokeKind.SLOW_LOAD:
        return _run_slow_load(case, page)
    if case.kind is SmokeKind.INFINITE_SCROLL:
        return _run_infinite_scroll(case, page)
    if case.kind is SmokeKind.LOGIN:
        return _run_login(case, page)
    return _run_http(case, http_client)


def run_smoke(
    cases: Iterable[SmokeCase] | None = None,
    *,
    page: Any = None,
    http_client: Any = None,
) -> SmokeReport:
    """跑一批用例。``http_client`` 必须提供（CLI 默认用 AntiRiskHttpClient）。"""
    case_list = list(cases) if cases is not None else list(DEFAULT_CASES)
    report = SmokeReport(started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    for case in case_list:
        report.add(run_case(case, page=page, http_client=http_client))
    report.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return report


# ── 默认用例（全部为公开练习站，实测可用）────────────

DEFAULT_CASES: tuple[SmokeCase, ...] = (
    SmokeCase(
        name="books_static",
        kind=SmokeKind.HTTP_STATIC,
        url="https://books.toscrape.com/",
        item_selector="article.product_pod",
        expected_min=20,
        expected_max=20,
        notes="静态 SSR，首页固定 20 本",
    ),
    SmokeCase(
        name="books_pagination",
        kind=SmokeKind.HTTP_PAGINATION,
        url="https://books.toscrape.com/",
        item_selector="article.product_pod",
        expected_min=20,
        expected_max=20,
        page2_url="https://books.toscrape.com/catalogue/page-2.html",
        expected_page2_min=20,
        notes="共 50 页 / 1000 本；第二页也必须拿满",
    ),
    SmokeCase(
        name="web_scraping_products",
        kind=SmokeKind.HTTP_PAGINATION,
        url="https://web-scraping.dev/products",
        item_selector="div.product",
        expected_min=5,
        expected_max=5,
        page2_url="https://web-scraping.dev/products?page=2",
        expected_page2_min=5,
        notes="现代站点，分页参数 ?page=N",
    ),
    SmokeCase(
        name="countries_big_list",
        kind=SmokeKind.BIG_LIST,
        url="https://www.scrapethissite.com/pages/simple/",
        item_selector="div.country",
        expected_min=250,
        expected_max=250,
        notes="250 个国家；验证 expected == actual，防漏条",
    ),
    SmokeCase(
        name="quotes_js_render",
        kind=SmokeKind.JS_RENDER,
        url="https://quotes.toscrape.com/js/",
        item_selector="div.quote",
        expected_min=10,
        expected_max=10,
        http_expected_max=0,
        notes="HTTP 原始 HTML 0 条 → 浏览器 10 条；证明不能把空壳当成功",
    ),
    SmokeCase(
        name="quotes_slow_load",
        kind=SmokeKind.SLOW_LOAD,
        url="https://quotes.toscrape.com/js-delayed?delay=10000",
        item_selector="div.quote",
        expected_min=10,
        expected_max=10,
        render_timeout_ms=25000,
        min_elapsed_ms=8000,
        notes="延迟 10s 渲染；超时不判失败，继续等",
    ),
    SmokeCase(
        name="quotes_infinite_scroll",
        kind=SmokeKind.INFINITE_SCROLL,
        url="https://quotes.toscrape.com/scroll",
        item_selector="div.quote",
        expected_min=10,
        scroll_rounds=6,
        notes="无限滚动；必须有停止条件，不能等最后一页",
    ),
    SmokeCase(
        name="saucedemo_login",
        kind=SmokeKind.LOGIN,
        url="https://www.saucedemo.com/",
        item_selector=".inventory_item",
        expected_min=6,
        login_user="standard_user",
        login_password="secret_sauce",
        login_success_selector=".inventory_item",
        notes="公开测试账号；验证登录表单 + 登录后列表",
    ),
    SmokeCase(
        name="quotes_negative_control",
        kind=SmokeKind.NEGATIVE_CONTROL,
        url="https://quotes.toscrape.com/",
        item_selector="div.quote-does-not-exist",
        expected_min=10,
        notes="故意写错选择器；0 条必须判 FAIL，否则检测层本身坏了",
    ),
)


__all__ = [
    "DEFAULT_CASES",
    "SmokeCase",
    "SmokeKind",
    "SmokeReport",
    "SmokeResult",
    "count_in_html",
    "run_case",
    "run_smoke",
]
