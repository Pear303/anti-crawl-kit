"""浏览器会话池 —— 复用 browser 进程，每次租用新建隔离 context。

为什么需要它
------------
MCP 连续调用（probe → recipe → smoke）如果每次都 ``BrowserSession()``，
每次都要重新启动 Chrome（3~5s）。浏览器进程完全可以复用，真正需要隔离的
只是 **cookie / storage**。

设计（重要：patchright sync API 有线程亲和性）
--------------------------------------------
- **不能**把一个线程创建的 page 交给另一个线程用；
- 所以池按**线程**持有 browser：同一线程连续租用会复用同一个 browser；
- 每次 ``lease()`` 新建一个 ``BrowserContext`` + ``Page``，退出时关闭；
- context 隔离 cookie / localStorage / IndexedDB，租约之间互不影响；
- 上限 ``max_sessions`` 控制并发浏览器进程数；
- ``idle_ttl`` 到期或 browser 断连时自动重建。

代价与边界：
- 复用 browser、不复用 context：省掉启动成本，保留隔离性；
- 需要登录态的持久化 profile 不适用本池，仍用 ``BrowserSession(data_dir=...)``；
- 跨线程不会共享 browser（这是 patchright 的限制，不是设计偷懒）。

用法::

    from anti_crawl_kit.browser_pool import BrowserPool

    pool = BrowserPool(max_sessions=2)
    with pool.lease() as page:
        page.goto("https://books.toscrape.com/")
        print(page.title())
    # 第二次租用：browser 复用，只新建 context
    with pool.lease() as page:
        ...
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger(__name__)

DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}


@dataclass
class PoolStats:
    leases: int = 0
    sessions_created: int = 0
    sessions_reused: int = 0
    contexts_created: int = 0
    lease_errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "leases": self.leases,
            "sessions_created": self.sessions_created,
            "sessions_reused": self.sessions_reused,
            "contexts_created": self.contexts_created,
            "lease_errors": self.lease_errors,
        }


@dataclass
class _ThreadSession:
    playwright: Any
    browser: Any
    created_at: float
    last_used: float
    channel: str = ""

    def close(self) -> None:
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self.playwright.stop()
        except Exception:
            pass


class BrowserPool:
    """按线程复用 browser，按租约隔离 context。"""

    def __init__(
        self,
        *,
        max_sessions: int = 2,
        headless: bool = True,
        idle_ttl: float = 600.0,
        channel: str | None = None,
        viewport: dict[str, int] | None = None,
        locale: str = "zh-CN",
        timezone: str = "Asia/Shanghai",
        user_agent: str = "",
        proxy: dict[str, Any] | None = None,
        stealth: bool = True,
        args: list[str] | None = None,
        browser_factory: Any = None,
    ) -> None:
        self.max_sessions = max(1, max_sessions)
        self.headless = headless
        self.idle_ttl = max(0.0, idle_ttl)
        self.channel = channel or ""
        self.viewport = viewport or DEFAULT_VIEWPORT
        self.locale = locale
        self.timezone = timezone
        self.user_agent = user_agent or ""
        self.proxy = proxy
        self.stealth = stealth
        self.args = args
        self._browser_factory = browser_factory

        self._semaphore = threading.BoundedSemaphore(self.max_sessions)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._stats = PoolStats()
        self._closed = False

    # ── 对外 ──
    @contextmanager
    def lease(self, *, stealth: bool | None = None) -> Iterator[Any]:
        """租用一个隔离页面。退出时关闭 context，保留 browser。"""
        if self._closed:
            raise RuntimeError("BrowserPool 已关闭")
        if not self._semaphore.acquire(blocking=True, timeout=60):
            raise TimeoutError("等待浏览器会话超时（max_sessions 已满）")

        context = None
        page = None
        try:
            session = self._get_session()
            context_args = self._context_args()
            context = session.browser.new_context(**context_args)
            with self._lock:
                self._stats.contexts_created += 1

            use_stealth = self.stealth if stealth is None else stealth
            if use_stealth:
                try:
                    from .stealth import STEALTH_JS

                    context.add_init_script(STEALTH_JS)
                except Exception as exc:  # stealth 失败不能阻塞主流程
                    logger.debug("[pool] stealth 注入失败: %r", exc)

            page = context.new_page()
            from .page import PageAdapter

            adapter = PageAdapter(page, anti_crawl=False)
            with self._lock:
                self._stats.leases += 1
            yield adapter
        except Exception:
            with self._lock:
                self._stats.lease_errors += 1
            raise
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            self._semaphore.release()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            data = self._stats.to_dict()
        data.update(
            {
                "max_sessions": self.max_sessions,
                "headless": self.headless,
                "idle_ttl": self.idle_ttl,
            }
        )
        return data

    def close_current_thread(self) -> None:
        """关闭当前线程持有的 browser（跨线程关闭不安全）。"""
        session: _ThreadSession | None = getattr(self._local, "session", None)
        if session is None:
            return
        session.close()
        self._local.session = None

    def shutdown(self) -> None:
        """标记关闭并释放当前线程的 browser；其他线程由进程退出回收。"""
        self._closed = True
        self.close_current_thread()

    # ── 内部 ──
    def _context_args(self) -> dict[str, Any]:
        args: dict[str, Any] = {
            "locale": self.locale,
            "timezone_id": self.timezone,
            "ignore_https_errors": True,
        }
        if self.user_agent:
            args["user_agent"] = self.user_agent
        if self.proxy:
            args["proxy"] = self.proxy
        if self.headless:
            args["viewport"] = self.viewport
            args["device_scale_factor"] = 1
        else:
            args["viewport"] = None
        return args

    def _get_session(self) -> _ThreadSession:
        session: _ThreadSession | None = getattr(self._local, "session", None)
        now = time.time()
        if session is not None:
            stale = self.idle_ttl > 0 and (now - session.last_used) > self.idle_ttl
            if stale or not self._is_connected(session.browser):
                session.close()
                session = None
                self._local.session = None
                logger.info("[pool] 会话过期/断连，重建 browser")

        if session is None:
            session = self._create_session()
            self._local.session = session
            with self._lock:
                self._stats.sessions_created += 1
        else:
            with self._lock:
                self._stats.sessions_reused += 1
        session.last_used = now
        return session

    @staticmethod
    def _is_connected(browser: Any) -> bool:
        try:
            return bool(browser.is_connected())
        except Exception:
            return False

    def _create_session(self) -> _ThreadSession:
        if self._browser_factory is not None:
            session = self._browser_factory()
            if not isinstance(session, _ThreadSession):
                raise TypeError("browser_factory 必须返回 _ThreadSession")
            return session

        try:
            from patchright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise RuntimeError(
                "需要 patchright：pip install 'anti-crawl-kit[browser]'"
            ) from exc

        from .session import CHROMIUM_ARGS, SYSTEM_BROWSER_CHANNELS

        playwright = sync_playwright().start()
        try:
            browser, channel = self._launch(playwright, CHROMIUM_ARGS, SYSTEM_BROWSER_CHANNELS)
        except Exception:
            try:
                playwright.stop()
            except Exception:
                pass
            raise
        now = time.time()
        logger.info("[pool] 新 browser 已启动 (channel=%s)", channel)
        return _ThreadSession(
            playwright=playwright,
            browser=browser,
            created_at=now,
            last_used=now,
            channel=channel,
        )

    def _launch(self, playwright: Any, default_args: list[str], channels: list[str]):
        args = list(self.args) if self.args is not None else list(default_args)
        if not self.headless and "--start-maximized" not in args:
            args.append("--start-maximized")
        channels = [self.channel] if self.channel else list(channels)
        last_error: Exception | None = None
        for channel in channels:
            try:
                browser = playwright.chromium.launch(
                    headless=self.headless,
                    args=args,
                    channel=channel,
                )
                return browser, channel
            except Exception as exc:
                last_error = exc
                logger.warning("[pool] 系统浏览器 %s 不可用: %s", channel, exc)
        raise RuntimeError(
            f"未找到可用系统浏览器（已尝试: {', '.join(channels)}）。"
            f"请安装 Chrome 或 Edge。最后错误: {last_error}"
        )


__all__ = ["BrowserPool", "PoolStats"]
