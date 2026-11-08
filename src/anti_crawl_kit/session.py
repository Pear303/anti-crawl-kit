"""patchright 浏览器会话管理器 —— 工作包的核心入口。

特性（均为实战验证过的默认值）：
  - 使用系统 Chrome / Edge（channel 自动降级），不用 Playwright 内置 Chromium
  - 持久化 user-data-dir，登录态复用
  - 有头模式最大化窗口、跟随系统 DPI；无头模式固定 viewport 保证截图清晰
  - patchright 驱动层反检测 + STEALTH_JS 兜底
  - CDP 接管已开浏览器（调试 / 复用登录态）
  - CDP 首导航自愈（版本 gap / 网络抖动导致空白转圈时自动 reload）
  - 关窗 watchdog（用户手动关窗 → 任务可感知）
  - atexit / SIGINT / SIGTERM 清理，避免 driver 子进程泄漏

用法::

    from anti_crawl_kit import BrowserSession

    with BrowserSession(headless=False, data_dir="./profiles/demo") as page:
        page.goto("https://example.com")
        print(page.title())
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from typing import Optional

from .page import PageAdapter

logger = logging.getLogger(__name__)

DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}


# ── 版本对齐（红线 1）────────────────────────────────
def _chrome_candidates() -> list[str]:
    """返回可能存在的 Chrome 可执行文件路径。"""
    candidates: list[str] = []
    if sys.platform == "win32":
        for base in (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if base:
                candidates.append(
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                )
    elif sys.platform == "darwin":
        candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    else:
        candidates.extend(["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"])
    return candidates


def _edge_candidates() -> list[str]:
    """返回可能存在的 Edge 可执行文件路径（Chrome 缺失时的降级目标）。"""
    candidates: list[str] = []
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
            if base:
                candidates.append(
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
                )
    elif sys.platform == "darwin":
        candidates.append("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    else:
        candidates.extend(["microsoft-edge", "microsoft-edge-stable"])
    return candidates


def _version_from_install_dir(executable: str) -> str | None:
    """Windows：从 ``Application/<version>/`` 子目录名读版本。"""
    app_dir = os.path.dirname(executable)
    if not os.path.isdir(app_dir):
        return None
    versions = [
        name
        for name in os.listdir(app_dir)
        if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", name)
    ]
    if not versions:
        return None
    return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))


def _detect_version(candidates: list[str]) -> str | None:
    import subprocess

    for exe in candidates:
        # Windows 的 chrome.exe / msedge.exe 不响应 --version，改读安装目录
        if sys.platform == "win32":
            version = _version_from_install_dir(exe)
            if version:
                return version
        try:
            proc = subprocess.run(
                [exe, "--version"], capture_output=True, text=True, timeout=5
            )
            text = (proc.stdout or proc.stderr or "").strip()
            match = re.search(r"(\d+\.\d+\.\d+\.\d+)", text)
            if match:
                return match.group(1)
        except Exception:
            continue
    return None


def detect_chrome_version(executable: str | None = None) -> str | None:
    """探测系统 Chrome 版本，例如 ``156.0.8078.74``；找不到返回 None。"""
    return _detect_version([executable] if executable else _chrome_candidates())


def detect_edge_version() -> str | None:
    """探测系统 Edge 版本；找不到返回 None。"""
    return _detect_version(_edge_candidates())


def detect_patchright_chromium() -> str | None:
    """读取 patchright 自带 browsers.json 中期望的 Chromium 版本。"""
    try:
        import patchright
        from pathlib import Path

        base = Path(patchright.__file__).resolve().parent
        for candidate in (
            base / "driver" / "package" / "browsers.json",
            base / "browsers.json",
        ):
            if not candidate.exists():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8"))
            for browser in data.get("browsers", []):
                if browser.get("name") == "chromium":
                    return browser.get("browserVersion")
    except Exception:
        return None
    return None

# ── 系统浏览器 channel 降级策略 ────────────────────────────
SYSTEM_BROWSER_CHANNELS = ["chrome", "msedge"]


def _system_browser_candidates(channel: str) -> list[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    if channel == "chrome":
        return [
            os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        ]
    if channel == "msedge":
        return [
            os.path.join(program_files, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(program_files_x86, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
    return []


# ── Chrome 启动参数 ────────────────────────────────────────
# 原则：
#   1. 只保留与「系统 Chrome/Edge」兼容的参数（--no-zygote 等 Linux 专用参数
#      在 Windows 系统 Chrome 上会导致白屏，已移除）。
#   2. 合并 --disable-features 为单条（同 key 重复时 Chrome 只取一条）。
#   3. --disk-cache-size 限制缓存总量：profile 缓存无限增长会让冷启动卡十几秒。
CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--high-dpi-support=1",
    "--disable-features=ChromeWhatsNewUI,TranslateUI,InterestFeedContentSuggestions,OptimizationGuideInBrowser,PrivacySandboxSettings4,MediaRouter,DialMediaRouteProvider",
    "--disable-sync",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-notifications",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-default-browser-check",
    "--no-first-run",
    "--no-pings",
    "--no-proxy-server",
    "--password-store=basic",
    "--disable-client-side-phishing-detection",
    "--disk-cache-size=268435456",
    "--disable-touch-adjustment",
    "--disable-touch-drag-drop",
    "--disable-network-qualities",
]


class PlaywrightNotInstalledError(RuntimeError):
    """patchright 未安装。"""


class BrowserSession:
    """patchright 会话管理器。

    Args:
        headless: 是否无头（默认 False；有头更接近真实用户）。
        data_dir: 持久化 user-data-dir（保存登录态）。不同平台用不同目录隔离。
        viewport: 无头视口；有头模式自动使用最大化窗口。
        user_agent: None/"" 表示跟随真实浏览器（强烈推荐）。
        locale / timezone: 浏览器语言与时区。
        stealth: 是否注入 STEALTH_JS（默认 True）。
        proxy: 例如 {"server": "http://127.0.0.1:8080"}。
        channel: "chrome" / "msedge"；None 自动降级。
        block_resources: 是否拦截图片/字体/媒体（登录/验证码场景必须 False）。
        cdp_endpoint: 例如 "http://127.0.0.1:9222"，接管已开浏览器。
        anti_crawl: 是否启用导航限频 + 风控页检测（默认 True）。
    """

    def __init__(
        self,
        headless: bool = False,
        data_dir: Optional[str] = None,
        viewport: Optional[dict] = None,
        user_agent: Optional[str] = None,
        locale: str = "zh-CN",
        timezone: str = "Asia/Shanghai",
        stealth: bool = True,
        proxy: Optional[dict] = None,
        channel: Optional[str] = None,
        block_resources: bool = False,
        resource_whitelist: Optional[list] = None,
        cdp_endpoint: str = "",
        anti_crawl: bool = True,
        skip_version_check: bool = False,
    ):
        self.headless = headless
        self.data_dir = data_dir
        self.viewport = viewport or DEFAULT_VIEWPORT
        # 约定：None/"" → 不传 UA，跟随真实浏览器
        self.user_agent = "" if user_agent is None else user_agent
        self.locale = locale
        self.timezone = timezone
        self.stealth = stealth
        self.proxy = proxy
        self.channel = channel
        self.block_resources = block_resources
        self.resource_whitelist = resource_whitelist or []
        self._cdp_endpoint = cdp_endpoint
        self.anti_crawl = anti_crawl
        self.skip_version_check = skip_version_check

        self._playwright = None
        self._browser = None
        self._context = None
        self._pw_page = None
        self._page_adapter: Optional[PageAdapter] = None
        self._atexit_registered = False
        self._browser_gone = False
        self._stop_watchdog = False
        self._watchdog_thread: Optional[threading.Thread] = None

    # ── 上下文管理 ───────────────────────────────────
    def __enter__(self) -> PageAdapter:
        self._register_atexit_guard()
        self._check_versions()
        try:
            from patchright.sync_api import sync_playwright
        except ImportError as exc:
            raise PlaywrightNotInstalledError(
                "patchright 未安装。请运行: pip install patchright"
            ) from exc

        self._playwright = sync_playwright().start()
        try:
            return self._enter_inner()
        except Exception:
            # __enter__ 抛异常时 with 不会调用 __exit__；
            # 必须手动 stop()，否则 run-driver 子进程常驻泄漏内存。
            self._cleanup_after_failed_enter()
            raise

    def _check_versions(self) -> None:
        """启动时打印 patchright / Chrome 版本，大版本差 ≥2 直接失败（红线 1）。"""
        if self.skip_version_check:
            return
        try:
            import patchright

            pr_version = getattr(patchright, "__version__", "unknown")
        except Exception:
            pr_version = "unknown"

        chrome = detect_chrome_version() or detect_edge_version()
        expected = detect_patchright_chromium()
        print(f"[env] patchright={pr_version} chrome={chrome or 'unknown'}")
        logger.info(
            "patchright=%s chrome=%s expected_chromium=%s",
            pr_version,
            chrome or "unknown",
            expected or "unknown",
        )

        if not chrome or not expected:
            return
        try:
            chrome_major = int(chrome.split(".")[0])
            expected_major = int(expected.split(".")[0])
        except (ValueError, IndexError):
            return

        gap = abs(chrome_major - expected_major)
        if gap >= 2:
            raise RuntimeError(
                f"[FAIL] Chrome 大版本 {chrome_major} 与 patchright 期望的 "
                f"Chromium {expected_major} 相差 {gap} 个大版本。"
                "这会导致 CDP 首导航完成事件丢失、页面空白转圈。"
                "请升级 patchright，或显式传 skip_version_check=True。"
            )
        if gap == 1:
            logger.warning(
                "[WARN] Chrome %s 与 patchright 期望 Chromium %s 相差 1 个大版本，建议升级 patchright",
                chrome_major,
                expected_major,
            )

    def _enter_inner(self) -> PageAdapter:
        # ── CDP 模式：接管已启动的浏览器 ──
        if self._cdp_endpoint:
            self._browser = self._playwright.chromium.connect_over_cdp(self._cdp_endpoint)
            if self._browser.contexts:
                self._context = self._browser.contexts[0]
                fit_viewport = self.viewport if self.headless else (self.viewport or DEFAULT_VIEWPORT)
                for p in self._context.pages:
                    try:
                        p.set_viewport_size(fit_viewport)
                    except Exception:
                        pass
            else:
                self._context = self._browser.new_context(
                    viewport=self.viewport if self.headless else None,
                    locale=self.locale,
                    timezone_id=self.timezone,
                    ignore_https_errors=True,
                )
            pages = self._context.pages
            if len(pages) > 1:
                for p in pages:
                    if p.url in ("about:blank", ""):
                        try:
                            p.close()
                        except Exception:
                            pass
                pages = [p for p in self._context.pages if p.url not in ("about:blank", "")]
            self._pw_page = pages[0] if pages else self._context.new_page()
            self._ensure_initial_navigation_ready(retries=2, timeout=7)
            return self._setup_page_and_return()

        # ── 独立模式 ──
        context_args: dict = {
            "locale": self.locale,
            "timezone_id": self.timezone,
            "ignore_https_errors": True,
        }
        if self.user_agent:
            context_args["user_agent"] = self.user_agent
        if self.proxy:
            context_args["proxy"] = self.proxy

        if self.headless:
            context_args["viewport"] = self.viewport
            context_args["device_scale_factor"] = 1
        else:
            # 有头：viewport=None 跟随最大化窗口真实尺寸，跟随系统 DPR
            context_args["viewport"] = None

        args = CHROMIUM_ARGS + (["--start-maximized"] if not self.headless else [])

        if self.data_dir:
            self._clear_persistent_zoom(self.data_dir)
            self._context = self._launch_with_fallback(
                lambda ch: self._playwright.chromium.launch_persistent_context(
                    self.data_dir,
                    headless=self.headless,
                    args=args,
                    **ch,
                    **context_args,
                )
            )
            self._browser = None
            self._pw_page = self._context.new_page()
            self._start_watchdog()
        else:
            self._browser = self._launch_with_fallback(
                lambda ch: self._playwright.chromium.launch(
                    headless=self.headless,
                    args=args,
                    **ch,
                )
            )
            self._context = self._browser.new_context(**context_args)
            self._pw_page = self._context.new_page()

        return self._setup_page_and_return()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_watchdog = True
        if self._cdp_endpoint:
            # CDP 只断开连接，不关闭用户浏览器进程
            if self._pw_page:
                try:
                    self._pw_page.close()
                except Exception:
                    pass
            if self._browser:
                try:
                    self._browser.close()
                except Exception:
                    pass
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
            self._unregister_atexit_guard()
            return False

        if self._pw_page:
            try:
                self._pw_page.close()
            except Exception:
                pass
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._unregister_atexit_guard()
        return False

    def close(self) -> None:
        """显式关闭别名（等价 __exit__）。"""
        self.__exit__(None, None, None)

    # ── 启动 ────────────────────────────────────────
    def _launch_with_fallback(self, launcher):
        """优先系统 Chrome，回退 Edge；全部失败给出清晰错误。"""
        channels = [self.channel] if self.channel else SYSTEM_BROWSER_CHANNELS
        last_error = None
        for channel in channels:
            try:
                result = launcher({"channel": channel})
                print(f"  [INFO] 使用系统浏览器: {channel}")
                return result
            except Exception as e:
                last_error = e
                print(f"  [WARN] 系统浏览器 {channel} 不可用 ({e})")
        raise RuntimeError(
            f"未找到任何可用的系统浏览器（已尝试: {', '.join(channels)}）。"
            f"请安装 Google Chrome 或 Microsoft Edge。\n最后错误: {last_error}"
        )

    # ── 页面装配 ─────────────────────────────────────
    def _setup_page_and_return(self) -> PageAdapter:
        if self.block_resources:
            try:
                from .resources import ResourceBlocker

                ResourceBlocker.apply_to_page(
                    self._pw_page, whitelist_domains=self.resource_whitelist
                )
            except Exception as exc:
                logger.warning("[resource-blocker] 应用失败: %r", exc)

        if self.stealth:
            from .stealth import STEALTH_JS

            self._pw_page.add_init_script(STEALTH_JS)

        # 统一重置 CSS zoom（防止 profile 持久化的极端缩放）
        try:
            self._pw_page.evaluate("() => { document.body.style.zoom = ''; }")
        except Exception:
            pass

        self._page_adapter = PageAdapter(self._pw_page, anti_crawl=self.anti_crawl)
        return self._page_adapter

    # ── CDP 首导航自愈 ──────────────────────────────
    def _ensure_initial_navigation_ready(self, retries: int = 2, timeout: float = 7.0) -> None:
        """CDP 接管后若页面停在空白/转圈，自动 reload。

        背景：patchright 与 Chrome 大版本不匹配（或网络抖动）时，首次导航
        可能丢失完成事件，页面一直空白，只能手动硬刷新。
        """
        page = self._pw_page
        if page is None:
            return
        try:
            if page.url in ("about:blank", ""):
                return
        except Exception:
            return
        for attempt in range(retries + 1):
            if self._wait_ready_state(page, timeout=timeout):
                return
            logger.warning("[cdp] 首导航未就绪，reload 重试 %d/%d", attempt + 1, retries)
            try:
                if page.is_closed():
                    return
                page.reload(timeout=int(timeout * 1000))
            except Exception:
                return

    @staticmethod
    def _wait_ready_state(page, timeout: float = 7.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                state = page.evaluate("() => document.readyState")
            except Exception:
                state = ""
            if state in ("interactive", "complete"):
                return True
            try:
                page.wait_for_timeout(200)
            except Exception:
                return False
        return False

    # ── 关窗 watchdog ───────────────────────────────
    def _start_watchdog(self) -> None:
        """持久化 context 没有 browser 对象，轮询 context.pages 判断浏览器是否存活。"""
        if self.headless or not self._context:
            return

        def _poll():
            while not self._stop_watchdog:
                time.sleep(5)
                try:
                    if not self._context.pages:
                        self._browser_gone = True
                        return
                except Exception:
                    self._browser_gone = True
                    return

        self._watchdog_thread = threading.Thread(target=_poll, daemon=True)
        self._watchdog_thread.start()

    def check_browser_alive(self) -> bool:
        """False = 窗口被用户关闭/崩溃。"""
        return not self._browser_gone

    # ── 清理保护 ────────────────────────────────────
    def _register_atexit_guard(self) -> None:
        if self._atexit_registered:
            return
        self._atexit_registered = True

        def _guard():
            try:
                self.__exit__(None, None, None)
            except Exception:
                pass

        atexit.register(_guard)
        self._atexit_fn = _guard

        def _sig_handler(signum, frame):
            try:
                self.__exit__(None, None, None)
            except Exception:
                pass
            sys.exit(128 + signum)

        self._original_signals = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._original_signals[sig] = signal.signal(sig, _sig_handler)
            except (OSError, ValueError):
                pass

    def _unregister_atexit_guard(self) -> None:
        if not self._atexit_registered:
            return
        try:
            atexit.unregister(self._atexit_fn)
        except Exception:
            pass
        for sig, original in getattr(self, "_original_signals", {}).items():
            try:
                signal.signal(sig, original)
            except (OSError, ValueError):
                pass
        self._atexit_registered = False

    def _cleanup_after_failed_enter(self) -> None:
        if self._pw_page:
            try:
                self._pw_page.close()
            except Exception:
                pass
        if self._context and not self._cdp_endpoint:
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser and not self._cdp_endpoint:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._unregister_atexit_guard()

    @staticmethod
    def _clear_persistent_zoom(data_dir: str) -> None:
        """清除 Chrome Preferences 里的 per_host_zoom_levels。

        某些场景（用户误操作 Ctrl+滚轮、页面 JS）会把缩放持久化成极端值，
        下次打开同一域名页面被缩放到 ~7%。
        """
        for prefs_path in (
            os.path.join(data_dir, "Default", "Preferences"),
            os.path.join(data_dir, "Preferences"),
        ):
            if not os.path.isfile(prefs_path):
                continue
            try:
                with open(prefs_path, "r", encoding="utf-8") as f:
                    prefs = json.load(f)
                partition = prefs.get("partition", {})
                phzl = partition.get("per_host_zoom_levels", {})
                if phzl.get("x"):
                    partition["per_host_zoom_levels"] = {"x": {}}
                    prefs["partition"] = partition
                    tmp_path = prefs_path + ".tmp"
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        json.dump(prefs, f, ensure_ascii=False)
                    os.replace(tmp_path, prefs_path)
            except Exception:
                pass

    # ── 属性 ────────────────────────────────────────
    @property
    def browser(self):
        return self._browser

    @property
    def context(self):
        return self._context

    @property
    def pw_page(self):
        return self._pw_page
