"""页面操作适配器 —— 在 patchright Page 之上加导航重试、反爬守卫、响应拦截。

为什么需要这一层：
    裸 page.goto() 没有重试、没有同域限频、没有风控页检测。
    项目里所有采集器统一走 PageAdapter，新平台接入时不用重复造轮子。
"""
from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅在类型检查时导入，未装 patchright 也能 import 本模块
    from patchright.sync_api import Page as PlaywrightPage

logger = logging.getLogger(__name__)

# 慢加载提示：不要把用户逼进「反复重开 → 风控冷却更长」的恶性循环。
_SLOW_LOAD_HINT = (
    "平台可能处于时段性惩罚窗口（同代码跨时段可差 3~6 倍）。"
    "请等待 1~2 分钟再试，不要反复重开浏览器——重开只会延长冷却。"
)


def _is_timeout_error(exc: BaseException) -> bool:
    return "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower()


class PageAdapter:
    """统一页面操作接口（仅 patchright，保持依赖最小）。"""

    def __init__(self, driver: PlaywrightPage, *, anti_crawl: bool = True):
        self._driver = driver
        self._capture_handlers: dict[str, object] = {}
        self._anti_crawl = anti_crawl
        self._guard = None
        # 最近一次导航的可观测指标（goto_ms / slow_load / ready_state /
        # recovered_from_timeout / attempts）。诊断与 bench 读这里，不截图。
        self.last_nav_metrics: dict[str, Any] = {}
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
        slow_load_ms: int | None = None,
        ensure_ready: bool = True,
    ):
        """导航到 URL，带指数退避重试 + reload + 首导航自愈。

        为什么不是「超时就关窗判失败」：
            五轮对照实验证明技术栈没有性能缺陷，慢是平台时段性惩罚窗口
            （passport challenge 挂起 9~13s，同代码跨时段差 3~6 倍）。
            旧实现一超时就关窗，用户反复重开 → 冷却越来越长。

        行为：
        - 首次 goto；失败后 reload（比重新 goto 快，且保留页面上下文）。
        - 退避公式：backoff_base * 2^attempt，上限 30s。
        - 反爬守卫只在首次导航前补延时，重试不重复延时。
        - ``goto`` 超时不直接判失败：先轮询 ``document.readyState``，
          已到 interactive/complete 就按「慢加载」继续。
        - ``about:blank`` 跳过自愈；``chrome-error://`` 视为可交互。
        - 指标写入 ``self.last_nav_metrics``，诊断/bench 读指标，不要截图
          （实测 screenshot() 会把 10~12s 拉到 25~40s，数据失真）。

        Returns:
            最后一次导航的 Response（可能为 None），供需要状态码的调用方使用。
        """
        if slow_load_ms is None:
            try:
                slow_load_ms = int(os.environ.get("AC_KIT_SLOW_LOAD_MS", "15000") or 15000)
            except ValueError:
                slow_load_ms = 15000

        self.last_nav_metrics = {
            "url": url,
            "attempts": 0,
            "goto_ms": 0,
            "ready_state": "",
            "slow_load": False,
            "recovered_from_timeout": False,
            "error": "",
        }
        last_error: Exception | None = None
        if self._guard is not None:
            self._guard.wait_nav_gap(url)

        for attempt in range(max_retries):
            started = time.perf_counter()
            try:
                if attempt == 0:
                    response = self._driver.goto(
                        url, wait_until=wait_until, timeout=timeout
                    )
                else:
                    response = self._driver.reload(
                        wait_until=wait_until, timeout=timeout
                    )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self.last_nav_metrics.update(attempts=attempt + 1, goto_ms=elapsed_ms)
                if elapsed_ms >= slow_load_ms:
                    self.last_nav_metrics["slow_load"] = True
                    logger.warning(
                        "[anti-crawl] 导航偏慢 %.1fs（阈值 %.1fs），继续等待而非判失败",
                        elapsed_ms / 1000,
                        slow_load_ms / 1000,
                    )
                if ensure_ready and not self._is_blank_url(url):
                    ready = self._ensure_initial_navigation_ready()
                    self.last_nav_metrics["ready_state"] = self._ready_state()
                    if not ready:
                        self.last_nav_metrics["slow_load"] = True
                        logger.warning(
                            "[anti-crawl] 首导航完成事件疑似丢失（readyState=%r），"
                            "继续尝试而不是立刻判失败",
                            self.last_nav_metrics["ready_state"],
                        )
                self._post_navigation_risk_check(url)
                return response
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self.last_nav_metrics.update(attempts=attempt + 1, goto_ms=elapsed_ms)
                last_error = exc
                is_timeout = _is_timeout_error(exc)
                if is_timeout:
                    self.last_nav_metrics["slow_load"] = True
                    # 慢加载 ≠ 失败：页面可能已经可交互，只是完成事件丢了。
                    if ensure_ready and self._ensure_initial_navigation_ready(
                        retries=1, timeout=7.0
                    ):
                        self.last_nav_metrics["recovered_from_timeout"] = True
                        self.last_nav_metrics["ready_state"] = self._ready_state()
                        logger.warning(
                            "[anti-crawl] goto 超时但页面已可交互，按慢加载继续: %s", url
                        )
                        self._post_navigation_risk_check(url)
                        return None
                self.last_nav_metrics["error"] = f"{type(exc).__name__}: {exc}"
                if attempt >= max_retries - 1:
                    if is_timeout:
                        raise TimeoutError(
                            f"导航超时 {timeout}ms（{url}）。{_SLOW_LOAD_HINT}"
                        ) from exc
                    raise
                wait = min(backoff_base * (2 ** attempt), 30.0)
                time.sleep(wait)
        if last_error:
            raise last_error
        return None

    def _is_blank_url(self, url: str) -> bool:
        return (url or "").strip().lower().startswith("about:blank")

    def _current_url(self) -> str:
        try:
            return str(self._driver.url or "")
        except Exception:
            return ""

    def _ready_state(self) -> str:
        """读取 document.readyState。

        ★ patchright 1.62.1 的 sync ``Page.evaluate`` **没有** ``timeout`` 参数，
        不要照 1.61.2 的写法传 timeout；这里也不包 PageAdapter.evaluate，
        避免 isolated_context 语义干扰。
        """
        try:
            return str(self._driver.evaluate("() => document.readyState") or "")
        except Exception:
            return ""

    def _ensure_initial_navigation_ready(
        self,
        retries: int = 2,
        timeout: float = 7.0,
    ) -> bool:
        """首导航自愈：等 readyState 到 interactive/complete。

        「空白转圈」根因是 patchright 版本 gap + 网络抖动导致首导航完成事件
        丢失，``goto`` 却返回成功。这里用显式轮询兜住。
        """
        try:
            if self._driver.is_closed():
                return False
        except Exception:
            pass

        current = self._current_url()
        if not current or self._is_blank_url(current):
            return True
        if current.startswith("chrome-error://"):
            return True

        for attempt in range(max(1, retries)):
            try:
                self._driver.wait_for_load_state(
                    "domcontentloaded", timeout=int(timeout * 1000)
                )
                return True
            except Exception:
                pass
            state = self._ready_state()
            if state in ("interactive", "complete"):
                return True
            current = self._current_url()
            if current.startswith("chrome-error://"):
                return True
            if attempt < retries - 1:
                try:
                    self._driver.wait_for_timeout(500)
                except Exception:
                    time.sleep(0.5)
        return self._ready_state() in ("interactive", "complete")

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
