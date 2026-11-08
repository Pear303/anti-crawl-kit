"""页面操作适配器 —— 在 patchright Page 之上加导航重试、反爬守卫、响应拦截。

为什么需要这一层：
    裸 page.goto() 没有重试、没有同域限频、没有风控页检测。
    项目里所有采集器统一走 PageAdapter，新平台接入时不用重复造轮子。
"""
from __future__ import annotations

import logging
import time

from patchright.sync_api import Page as PlaywrightPage

logger = logging.getLogger(__name__)


class PageAdapter:
    """统一页面操作接口（仅 patchright，保持依赖最小）。"""

    def __init__(self, driver: PlaywrightPage, *, anti_crawl: bool = True):
        self._driver = driver
        self._capture_handlers: dict[str, object] = {}
        self._anti_crawl = anti_crawl
        self._guard = None
        if anti_crawl:
            try:
                from .guard import get_global_guard

                self._guard = get_global_guard()
            except Exception:
                self._guard = None

    # ── 常用透传 ─────────────────────────────────────
    def __getattr__(self, name):
        return getattr(self._driver, name)

    @property
    def raw(self) -> PlaywrightPage:
        """底层 patchright Page（高级操作 / 事件监听用）。"""
        return self._driver

    @property
    def is_playwright(self) -> bool:
        return True

    # ── 导航 ────────────────────────────────────────
    def goto(
        self,
        url: str,
        wait_until: str = "domcontentloaded",
        timeout: int = 20000,
        max_retries: int = 3,
        backoff_base: float = 2.0,
    ):
        """导航到 URL，带指数退避重试 + reload。

        - 首次 goto；失败后 reload（比重新 goto 快，且保留页面上下文）。
        - 退避公式：backoff_base * 2^attempt，上限 30s。
        - 反爬守卫只在首次导航前补延时，重试不重复延时。
        - 返回最后一次导航的 Response（可能为 None），供需要状态码的调用方使用。
        """
        last_error: Exception | None = None
        if self._guard is not None:
            self._guard.wait_nav_gap(url)
        for attempt in range(max_retries):
            try:
                if attempt == 0:
                    response = self._driver.goto(
                        url, wait_until=wait_until, timeout=timeout
                    )
                else:
                    response = self._driver.reload(
                        wait_until=wait_until, timeout=timeout
                    )
                self._post_navigation_risk_check(url)
                return response
            except Exception as e:
                last_error = e
                if attempt >= max_retries - 1:
                    raise
                wait = min(backoff_base * (2 ** attempt), 30.0)
                time.sleep(wait)
        if last_error:
            raise last_error
        return None

    def _post_navigation_risk_check(self, url: str) -> None:
        """后导航风控检测：命中只记录日志，不打断采集流程。"""
        if self._guard is None or not self._driver:
            return
        try:
            result = self._guard.detect_risk_page(self._driver, url)
            if result and result.get("detected"):
                logger.warning(
                    "[anti-crawl] 检测到风控页: kind=%s reason=%s url=%s",
                    result.get("kind"),
                    result.get("reason"),
                    url,
                )
        except Exception:
            pass

    # ── JS ──────────────────────────────────────────
    def evaluate(self, script: str, arg=None, *, isolated_context: bool = True):
        """执行 JS 并返回结果。

        ★ patchright 特有：默认在隔离世界执行。
        读取页面主世界状态（window.xxx / __INITIAL_STATE__ / __vue_app__）
        必须传 isolated_context=False。

        arg 会作为第二个参数传给页面函数（与 Playwright 原生一致）。
        """
        if arg is None:
            return self._driver.evaluate(script, isolated_context=isolated_context)
        return self._driver.evaluate(script, arg, isolated_context=isolated_context)

    def execute_script(self, script: str) -> None:
        self._driver.evaluate(script)

    # ── 等待 ────────────────────────────────────────
    def wait_for_selector(self, selector: str, timeout: int = 10000) -> bool:
        try:
            self._driver.wait_for_selector(selector, timeout=timeout)
            return True
        except Exception:
            return False

    # ── 网络响应拦截（新平台接口发现的核心手段）──────────
    def capture_responses(self, url_pattern: str) -> list[dict]:
        """拦截匹配 URL 子串的 JSON 响应，返回持续填充的列表。

        用法::

            captured = page.capture_responses("/api/search")
            page.goto("https://example.com/search?q=xx")
            page.wait_for_timeout(3000)
            page.stop_capture("/api/search")
            for item in captured: ...
        """
        captured: list[dict] = []

        def on_response(response):
            try:
                if url_pattern in response.url:
                    captured.append(response.json())
            except Exception:
                pass

        self._driver.on("response", on_response)
        self._capture_handlers[url_pattern] = on_response
        return captured

    def stop_capture(self, url_pattern: str) -> None:
        handler = self._capture_handlers.pop(url_pattern, None)
        if handler:
            try:
                self._driver.remove_listener("response", handler)
            except Exception:
                pass

    # ── Cookie / 请求 ───────────────────────────────
    def get_cookies(self) -> list[dict]:
        return self._driver.context.cookies()

    def api_request(self, url: str, headers: dict | None = None, timeout: int = 10000) -> dict:
        """通过 Playwright 原生请求 API 发请求（自动携带浏览器 cookie）。"""
        resp = self._driver.context.request.fetch(
            url, headers=headers or {}, timeout=timeout
        )
        try:
            return resp.json()
        except Exception:
            return {
                "code": -999,
                "message": "Non-JSON response",
                "body": resp.text()[:500],
                "status": resp.status,
            }
