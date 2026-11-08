"""集中化反爬守卫 —— 在 PageAdapter.goto 层统一注入反爬能力。

设计目标：让所有经过 PageAdapter 的导航自动获得三类能力，无需每个采集器
各写一遍：

  1. 预导航随机延时：每次 goto 前按「距上次同域导航间隔」补齐随机延时，
     消除固定节奏的机器特征。
  2. 后导航风控检测：加载完成后检测验证码/风控页（URL + DOM 双路径）。
  3. 同域导航限频：同一域名相邻两次导航之间保证最小间隔。

配置（环境变量，默认开启，全部可关）：
    AC_KIT_ANTI_CRAWL_ENABLED=0    整体关闭
    AC_KIT_ANTI_CRAWL_DELAY=0      关闭预导航随机延时
    AC_KIT_ANTI_CRAWL_DETECT=0     关闭后导航风控检测
    AC_KIT_ANTI_CRAWL_RATELIMIT=0  关闭同域导航限频

注意：守卫只能「降低频率特征」，不能替代真实浏览器环境和登录态。
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_ENV_ENABLED = os.getenv("AC_KIT_ANTI_CRAWL_ENABLED", "1") != "0"
_ENV_DELAY = os.getenv("AC_KIT_ANTI_CRAWL_DELAY", "1") != "0"
_ENV_DETECT = os.getenv("AC_KIT_ANTI_CRAWL_DETECT", "1") != "0"
_ENV_RATELIMIT = os.getenv("AC_KIT_ANTI_CRAWL_RATELIMIT", "1") != "0"

_DEFAULT_MIN_INTERVAL = 1.0
_DEFAULT_MAX_INTERVAL = 4.0
_RISK_PAGE_KEYWORDS = (
    "captcha",
    "verify",
    "challenge",
    "slider",
    "安全验证",
    "验证码",
    "安全检查",
    "登录后",
    "请登录",
)

_BACKOFF_MIN = 3.0
_BACKOFF_MAX = 15.0


class AntiCrawlGuard:
    """集中化反爬守卫：包装页面导航，统一注入延时与风控检测。"""

    _global_lock = threading.Lock()
    _global_last_nav: dict[str, float] = {}

    def __init__(
        self,
        *,
        enabled: bool = True,
        enable_delay: bool = True,
        enable_detect: bool = True,
        enable_ratelimit: bool = True,
        min_interval: float = _DEFAULT_MIN_INTERVAL,
        max_interval: float = _DEFAULT_MAX_INTERVAL,
        isolate: bool = False,
        platform: str | None = None,
    ):
        self.enabled = enabled and _ENV_ENABLED
        self.enable_delay = enable_delay and _ENV_DELAY and self.enabled
        self.enable_detect = enable_detect and _ENV_DETECT and self.enabled
        self.enable_ratelimit = enable_ratelimit and _ENV_RATELIMIT and self.enabled
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.isolate = isolate
        self.platform = platform
        self._nav_times: dict[str, float] = {}

    @staticmethod
    def _domain_of(url: str) -> str:
        url = (url or "").strip()
        if not url or url.startswith("about:"):
            return "__none__"
        rest = url.split("://", 1)[-1]
        host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        host = host.rsplit("@", 1)[-1]
        return host.lower() or "__none__"

    def wait_nav_gap(self, url: str) -> None:
        """导航前调用：按「距上次同域导航间隔」补齐随机延时。"""
        if not self.enable_delay:
            return
        try:
            domain = self._domain_of(url)
            table = self._nav_times if self.isolate else AntiCrawlGuard._global_last_nav
            now = time.monotonic()
            last = table.get(domain)
            if last is not None:
                elapsed = now - last
                target = random.uniform(self.min_interval, self.max_interval)
                if elapsed < target:
                    time.sleep(target - elapsed)
            else:
                time.sleep(random.uniform(0.2, 0.8))
            with AntiCrawlGuard._global_lock:
                table[domain] = time.monotonic()
        except Exception as exc:
            logger.debug("anti-crawl wait_nav_gap skipped: %r", exc)

    def wait_backoff(self, reason: str, attempt: int = 0) -> None:
        """风控/失败后的退避等待（比正常间隔更长）。"""
        if not self.enable_delay:
            return
        delay = min(_BACKOFF_MAX, _BACKOFF_MIN * (2 ** min(attempt, 3)))
        delay *= random.uniform(0.8, 1.2)
        logger.info("[anti-crawl] 风控退避 %.1fs (%s)", delay, reason)
        time.sleep(delay)

    def detect_risk_page(self, page: Any, url: str) -> dict:
        """加载完成后检测风控页。返回 {detected, kind, reason}。

        kind: "captcha" / "blocked" / "login_required" / "ok"
        """
        if not self.enable_detect:
            return {"detected": False, "kind": "ok", "reason": "detect_disabled"}
        try:
            lowered_url = (url or "").lower()
            for kw in _RISK_PAGE_KEYWORDS:
                if kw in lowered_url:
                    return {
                        "detected": True,
                        "kind": (
                            "captcha"
                            if any(
                                k in lowered_url
                                for k in ("captcha", "verify", "challenge", "slider", "验证码")
                            )
                            else "blocked"
                        ),
                        "reason": f"url:{kw}",
                    }
            return self._detect_risk_via_dom(page)
        except Exception as exc:
            logger.debug("anti-crawl detect_risk_page error: %r", exc)
            return {"detected": False, "kind": "ok", "reason": f"detect_error:{exc!r}"}

    def _detect_risk_via_dom(self, page: Any) -> dict:
        if not hasattr(page, "evaluate"):
            return {"detected": False, "kind": "ok", "reason": "no_evaluate"}
        try:
            result = page.evaluate(
                """() => {
                    var t = (document.title || '');
                    var body = (document.body && document.body.innerText) || '';
                    if (t.indexOf('安全验证') !== -1 || body.indexOf('安全验证') !== -1 ||
                        body.indexOf('请完成验证') !== -1 || body.indexOf('验证码') !== -1 ||
                        body.indexOf('拖动滑块') !== -1 || body.indexOf('向右滑动') !== -1) {
                        return { detected: true, kind: 'captcha', reason: 'dom_captcha' };
                    }
                    if (t.indexOf('访问异常') !== -1 || body.indexOf('访问异常') !== -1 ||
                        t.indexOf('请求过于频繁') !== -1 || body.indexOf('请求过于频繁') !== -1 ||
                        t.indexOf('429') !== -1 || body.indexOf('Please wait a moment') !== -1) {
                        return { detected: true, kind: 'blocked', reason: 'dom_blocked' };
                    }
                    return { detected: false, kind: 'ok', reason: '' };
                }"""
            )
            if isinstance(result, dict):
                return {
                    "detected": bool(result.get("detected", False)),
                    "kind": result.get("kind", "ok"),
                    "reason": result.get("reason", ""),
                }
            return {"detected": False, "kind": "ok", "reason": ""}
        except Exception as exc:
            return {"detected": False, "kind": "ok", "reason": f"dom_error:{exc!r}"}


_global_guard: AntiCrawlGuard | None = None
_global_guard_lock = threading.Lock()


def get_global_guard() -> AntiCrawlGuard:
    """获取进程级全局守卫（同域限频跨采集器共享）。"""
    global _global_guard
    if _global_guard is None:
        with _global_guard_lock:
            if _global_guard is None:
                _global_guard = AntiCrawlGuard()
    return _global_guard
