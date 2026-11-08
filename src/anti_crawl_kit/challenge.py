"""验证墙硬停闸门 —— 检测到人机验证就「保存现场 + 通知 + 抛异常」。

和 `wait_for_captcha_and_login` 的区别：

    wait_for_captcha_and_login：检测到验证码后**等人**，有独立预算，适合首次登录。
    ChallengeGate            ：检测到验证码后**立即停**，保存现场并通知，绝不重试。
                               适合「批量加载/翻页」过程中守住止损线。

设计原则：
    - 只做「止损」，不做「破解」。
    - 误报的代价是少采一批；漏报的代价是账号被标记。宁可偏保守。
    - 每次停下都留三件套：截图、HTML、meta.json，方便事后判断是验证码还是改版。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .captcha import get_platform_captcha_dom_js
from .notify import ChallengeInfo, Notifier, notify_challenge

logger = logging.getLogger(__name__)

_BLOCKED_STATUSES = {403, 412, 418, 429, 521}

_URL_CAPTCHA_KEYWORDS = (
    "captcha",
    "verify",
    "challenge",
    "slider",
    "security-check",
    "antispider",
    "anti-spider",
    "_____tmd_____",
    "punish",
)
_URL_BLOCKED_KEYWORDS = ("risk-control", "riskcheck", "blocked", "denied")

# 只匹配「可见」的验证组件，避免命中隐藏模板。
_CAPTCHA_SELECTORS = (
    "#cf-challenge-running",
    ".cf-browser-verification",
    ".challenge-form",
    "#challenge-platform",
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="cdn-cgi/challenge-platform"]',
    ".geetest_widget",
    ".geetest_panel",
    ".geetest_holder",
    ".geetest_slider",
    ".nc_1_wrapper",
    ".nc-container",
    "#nocaptcha",
    ".slider-verify",
    ".baxia-dialog",
    ".secsdk-captcha-drag-icon",
    ".Captcha-englishContainer",
    ".SignFlow-verifyCode",
    "#pass-verify",
    ".pass-verify",
    ".vercode",
    '[class*="captcha"][class*="modal"]',
    '[id*="captcha"][class*="modal"]',
)

_TITLE_CAPTCHA_KEYWORDS = (
    "安全验证",
    "安全认证",
    "人机验证",
    "请完成验证",
    "验证码",
    "Just a moment",
    "Attention Required",
    "Checking your browser",
    "Unusual traffic",
)
_TITLE_BLOCKED_KEYWORDS = (
    "访问异常",
    "请求过于频繁",
    "操作频繁",
    "环境异常",
    "Access Denied",
    "Too Many Requests",
)

_BODY_CAPTCHA_KEYWORDS = (
    "安全验证",
    "请完成验证",
    "请进行安全验证",
    "拖动滑块",
    "向右滑动",
    "完成拼图",
    "人机验证",
    "验证后继续",
    "百度安全验证",
    "Checking your browser",
    "needs to review the security",
)
_BODY_BLOCKED_KEYWORDS = (
    "访问异常",
    "请求过于频繁",
    "操作频繁",
    "环境异常",
    "网络环境存在异常",
    "Unusual traffic",
    "Too Many Requests",
)

_DOM_PROBE_JS = """
(opts) => {
  function visible(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width <= 2 || r.height <= 2) return false;
    var s = window.getComputedStyle(el);
    if (!s) return false;
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    if (parseFloat(s.opacity) === 0) return false;
    return true;
  }

  var selectors = %s;
  for (var i = 0; i < selectors.length; i++) {
    var nodes = document.querySelectorAll(selectors[i]);
    for (var j = 0; j < nodes.length; j++) {
      if (visible(nodes[j])) {
        return {detected: true, kind: 'captcha', reason: 'dom:' + selectors[i],
                title: document.title || ''};
      }
    }
  }

  var title = document.title || '';
  var titleCaptcha = %s;
  for (var k = 0; k < titleCaptcha.length; k++) {
    if (title.indexOf(titleCaptcha[k]) !== -1) {
      return {detected: true, kind: 'captcha', reason: 'title:' + titleCaptcha[k], title: title};
    }
  }
  var titleBlocked = %s;
  for (var m = 0; m < titleBlocked.length; m++) {
    if (title.indexOf(titleBlocked[m]) !== -1) {
      return {detected: true, kind: 'blocked', reason: 'title:' + titleBlocked[m], title: title};
    }
  }

  var body = (document.body && document.body.innerText) || '';
  if (opts && opts.scanBody && body.length <= (opts.maxChars || 4000)) {
    var bodyCaptcha = %s;
    for (var n = 0; n < bodyCaptcha.length; n++) {
      if (body.indexOf(bodyCaptcha[n]) !== -1) {
        return {detected: true, kind: 'captcha', reason: 'body:' + bodyCaptcha[n], title: title};
      }
    }
    var bodyBlocked = %s;
    for (var p = 0; p < bodyBlocked.length; p++) {
      if (body.indexOf(bodyBlocked[p]) !== -1) {
        return {detected: true, kind: 'blocked', reason: 'body:' + bodyBlocked[p], title: title};
      }
    }
  }

  return {detected: false, kind: 'ok', reason: '', title: title};
}
""" % (
    json.dumps(list(_CAPTCHA_SELECTORS), ensure_ascii=False),
    json.dumps(list(_TITLE_CAPTCHA_KEYWORDS), ensure_ascii=False),
    json.dumps(list(_TITLE_BLOCKED_KEYWORDS), ensure_ascii=False),
    json.dumps(list(_BODY_CAPTCHA_KEYWORDS), ensure_ascii=False),
    json.dumps(list(_BODY_BLOCKED_KEYWORDS), ensure_ascii=False),
)


class ChallengeDetected(RuntimeError):
    """检测到验证墙/风控墙。携带现场信息，调用方可直接退出。"""

    def __init__(self, info: ChallengeInfo):
        self.info = info
        where = info.screenshot or info.meta_path or info.url
        super().__init__(
            f"检测到人机验证/风控墙（{info.kind}: {info.reason}），已停止自动化。现场: {where}"
        )


class ChallengeGate:
    """验证墙硬停闸门。

    用法::

        gate = ChallengeGate(platform="xiaohongshu", notifier=Notifier())
        page.goto(url)
        gate.check_or_stop(page)          # 命中 → 截图 + 通知 + 抛 ChallengeDetected
        ...每次滚动/点击前都调用...
    """

    def __init__(
        self,
        *,
        platform: str = "",
        notifier: Notifier | None = None,
        snapshot_dir: str | Path = "output/challenge",
        scan_url: bool = True,
        scan_body: bool = False,
        body_scan_max_chars: int = 4000,
        blocked_statuses: set[int] | None = None,
        stop_on_login: bool = True,
        login_js: str | None = None,
        extra_url_keywords: tuple[str, ...] = (),
        extra_title_keywords: tuple[str, ...] = (),
        max_snapshots: int = 20,
        notify: bool = True,
    ):
        self.platform = (platform or "").lower()
        self.notifier = notifier or Notifier()
        self.snapshot_dir = Path(snapshot_dir)
        self.scan_url = scan_url
        self.scan_body = scan_body
        self.body_scan_max_chars = body_scan_max_chars
        self.blocked_statuses = blocked_statuses or set(_BLOCKED_STATUSES)
        self.stop_on_login = stop_on_login
        self.login_js = login_js
        self.extra_url_keywords = tuple(k.lower() for k in extra_url_keywords)
        self.extra_title_keywords = tuple(extra_title_keywords)
        self.max_snapshots = max_snapshots
        self.notify = notify

    # ── 检测 ────────────────────────────────────────
    def check(self, page: Any, *, response_status: int | None = None) -> ChallengeInfo:
        """只检测，不抛异常。返回 ChallengeInfo(detected=False/True)。"""
        url = self._safe_url(page)
        info = ChallengeInfo(
            detected=False,
            kind="ok",
            reason="",
            url=url,
            status=response_status,
            platform=self.platform,
        )

        # 1. HTTP 状态（最便宜）
        if response_status == 401 and self.stop_on_login:
            return replace(info, detected=True, kind="login_required", reason="http_401")
        if response_status in self.blocked_statuses:
            return replace(info, detected=True, kind="blocked", reason=f"http_{response_status}")

        # 2. URL 关键词（文章 URL 含 verify/captcha 时可关掉，避免误停）
        if self.scan_url:
            hit = self._url_hit(url)
            if hit:
                return replace(info, detected=True, kind=hit[0], reason=hit[1])

        # 3. DOM + 标题（+ 可选正文）
        dom = self._probe_dom(page)
        title = dom.get("title", "") if isinstance(dom, dict) else ""
        info = replace(info, title=title)
        if isinstance(dom, dict) and dom.get("detected"):
            return replace(
                info,
                detected=True,
                kind=dom.get("kind", "captcha"),
                reason=dom.get("reason", "dom"),
            )

        # 4. 平台专用验证码 DOM
        platform_js = get_platform_captcha_dom_js(self.platform)
        if platform_js:
            try:
                result = page.evaluate(platform_js)
                if isinstance(result, dict) and result.get("detected"):
                    return replace(
                        info,
                        detected=True,
                        kind="captcha",
                        reason=result.get("reason", f"platform:{self.platform}"),
                    )
            except Exception as exc:
                logger.debug("platform captcha probe failed: %r", exc)

        # 5. 登录态（可选）
        if self.stop_on_login and self.login_js:
            try:
                logged_in = bool(page.evaluate(self.login_js, isolated_context=False))
            except Exception:
                logged_in = True  # 探测失败不误报登录墙
            if not logged_in:
                return replace(
                    info, detected=True, kind="login_required", reason="login_js_false"
                )

        return info

    def check_or_stop(self, page: Any, *, response_status: int | None = None) -> None:
        """检测到就保存现场 + 通知 + 抛 ChallengeDetected；否则静默返回。"""
        info = self.check(page, response_status=response_status)
        if info.detected:
            self.stop(page, info)

    def stop(self, page: Any, info: ChallengeInfo) -> None:
        """保存现场、发通知、抛异常。"""
        info = self._snapshot(page, info)
        if self.notify:
            try:
                notify_challenge(info, self.notifier)
            except Exception as exc:
                logger.warning("通知发送失败（不影响停止）: %r", exc)
        raise ChallengeDetected(info)

    # ── 内部工具 ────────────────────────────────────
    @staticmethod
    def _safe_url(page: Any) -> str:
        try:
            return str(page.evaluate("() => window.location.href") or "")
        except Exception:
            try:
                return str(getattr(page, "url", "") or "")
            except Exception:
                return ""

    def _url_hit(self, url: str) -> tuple[str, str] | None:
        lowered = (url or "").lower()
        for kw in self.extra_url_keywords:
            if kw and kw in lowered:
                return ("captcha", f"url:{kw}")
        for kw in _URL_CAPTCHA_KEYWORDS:
            if kw in lowered:
                return ("captcha", f"url:{kw}")
        for kw in _URL_BLOCKED_KEYWORDS:
            if kw in lowered:
                return ("blocked", f"url:{kw}")
        return None

    def _probe_dom(self, page: Any) -> dict:
        try:
            result = page.evaluate(
                _DOM_PROBE_JS,
                {"scanBody": self.scan_body, "maxChars": self.body_scan_max_chars},
            )
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            logger.debug("dom challenge probe failed: %r", exc)
            return {}

    def _snapshot(self, page: Any, info: ChallengeInfo) -> ChallengeInfo:
        """落盘截图/HTML/meta，返回带路径的新 info。"""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        stem = f"{stamp}_{info.kind}"
        try:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return info

        png = self.snapshot_dir / f"{stem}.png"
        html = self.snapshot_dir / f"{stem}.html"
        meta = self.snapshot_dir / f"{stem}.json"

        screenshot_ok = ""
        try:
            page.screenshot(path=str(png), full_page=True)
            screenshot_ok = str(png)
        except Exception as exc:
            logger.debug("challenge screenshot failed: %r", exc)

        html_ok = ""
        try:
            content = page.content()
            html.write_text(content or "", encoding="utf-8", errors="replace")
            html_ok = str(html)
        except Exception as exc:
            logger.debug("challenge html dump failed: %r", exc)

        new_info = replace(info, screenshot=screenshot_ok, html=html_ok, meta_path=str(meta))
        try:
            meta.write_text(
                json.dumps(new_info.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("challenge meta dump failed: %r", exc)
            new_info = replace(new_info, meta_path="")

        self._prune()
        return new_info

    def _prune(self) -> None:
        try:
            metas = sorted(
                self.snapshot_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in metas[self.max_snapshots :]:
                for f in self.snapshot_dir.glob(f"{old.stem}.*"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
        except Exception:
            pass


def challenge_now(
    page: Any,
    *,
    platform: str = "",
    notifier: Notifier | None = None,
    snapshot_dir: str | Path = "output/challenge",
    response_status: int | None = None,
) -> ChallengeInfo:
    """一次性检测（不抛异常），适合放在重试循环的条件判断里。"""
    gate = ChallengeGate(
        platform=platform, notifier=notifier, snapshot_dir=snapshot_dir
    )
    return gate.check(page, response_status=response_status)
