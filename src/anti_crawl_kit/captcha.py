"""验证码/风控页检测与等待工具。

检测顺序（成本从低到高）：
    1. URL 关键词（captcha / verify / challenge / slider / 手机绑定）
    2. 平台专用 DOM 特征（Cloudflare / 极验 / 知乎 / 百家号）

实战原则：
    - 不要尝试自动破解滑块；检测到就提示用户手动完成，轮询等待消失。
    - 等待必须有独立预算，不能和登录预算互相挤占（见 waiting.py）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CaptchaDetection:
    detected: bool
    reason: str = ""
    url: str = ""


_CAPTCHA_URL_KEYWORDS = ["captcha", "verify", "challenge", "slider"]

# ── 平台专用 DOM 检测 JS 注册表 ─────────────────────────────
_CSDN_CF_DETECT_JS = """(() => {
    var title = (document.title || '');
    var titleKws = ['安全验证', '安全认证', 'Security', 'Just a moment', 'Attention', '请完成验证', 'Unusual traffic'];
    for (var i = 0; i < titleKws.length; i++) { if (title.indexOf(titleKws[i]) !== -1) return { detected: true, reason: 'cf_title' }; }

    var cfEl = document.querySelector(
        '#cf-challenge-running, .cf-browser-verification, .challenge-form, ' +
        '#cf-please-wait, #cf-spinner-please-wait, .cf-spinner-please-wait, ' +
        '#challenge-platform, .cf_challenge, #cf-challenge-widget, ' +
        'iframe[src*="challenges.cloudflare.com"], iframe[src*="cdn-cgi/challenge-platform"]'
    );
    if (cfEl) return { detected: true, reason: 'cf_dom' };

    var body = (document.body && document.body.innerText) || '';
    var bodyKws = ['安全验证', '安全认证', '请完成验证', '请进行安全验证', 'Checking your browser', 'needs to review the security', 'Performing a security check'];
    for (var j = 0; j < bodyKws.length; j++) { if (body.indexOf(bodyKws[j]) !== -1) return { detected: true, reason: 'cf_body' }; }

    return { detected: false };
})()"""

_ZHIHU_CAPTCHA_DETECT_JS = (
    "() => {"
    "  function vis(el) {"
    "    if (!el) return false;"
    "    var r = el.getBoundingClientRect();"
    "    if (r.width === 0 || r.height === 0) return false;"
    "    var s = window.getComputedStyle(el);"
    "    if (s.display === 'none' || s.visibility === 'hidden') return false;"
    "    if (parseFloat(s.opacity) === 0) return false;"
    "    return true;"
    "  }"
    "  var el;"
    "  el = document.querySelector('.Captcha-englishContainer');"
    "  if (el && vis(el)) return { detected: true, reason: 'captcha_dom:Captcha-englishContainer' };"
    "  el = document.querySelector('.SignFlow-verifyCode');"
    "  if (el && vis(el)) return { detected: true, reason: 'captcha_dom:SignFlow-verifyCode' };"
    "  var t = (document.title || '');"
    "  if (t.indexOf('安全验证') !== -1) return { detected: true, reason: 'zhihu_title' };"
    "  return { detected: false };"
    "}"
)

_BAIJIAHAO_CAPTCHA_DETECT_JS = (
    "() => {"
    "  function vis(el) {"
    "    if (!el) return false;"
    "    var r = el.getBoundingClientRect();"
    "    if (r.width === 0 || r.height === 0) return false;"
    "    var s = window.getComputedStyle(el);"
    "    if (s.display === 'none' || s.visibility === 'hidden') return false;"
    "    return true;"
    "  }"
    "  var t = (document.title || '');"
    "  if (t.indexOf('百度安全验证') !== -1 || t.indexOf('百度安全') !== -1) return { detected: true, reason: 'baijiahao_title' };"
    "  var body = (document.body && document.body.innerText) || '';"
    "  if (body.indexOf('百度安全验证') !== -1 || body.indexOf('请完成下方验证后继续操作') !== -1) {"
    "    return { detected: true, reason: 'baijiahao_body' };"
    "  }"
    "  var el;"
    "  el = document.querySelector('.v-bk, [class*=\"pass-verify\"], #pass-verify, .vercode');"
    "  if (el && vis(el)) return { detected: true, reason: 'baijiahao_dom' };"
    "  return { detected: false };"
    "}"
)

_GEETEST_CAPTCHA_DETECT_JS = (
    "() => {"
    "  function vis(el) {"
    "    if (!el) return false;"
    "    var r = el.getBoundingClientRect();"
    "    if (r.width === 0 || r.height === 0) return false;"
    "    var s = window.getComputedStyle(el);"
    "    if (s.display === 'none' || s.visibility === 'hidden') return false;"
    "    return true;"
    "  }"
    "  var el;"
    "  el = document.querySelector('.geetest_widget, .geetest_panel, .geetest_holder, .geetest_slider');"
    "  if (el && vis(el)) return { detected: true, reason: 'geetest_dom' };"
    "  var body = (document.body && document.body.innerText) || '';"
    "  if (body.indexOf('拖动滑块') !== -1 || body.indexOf('向右滑动') !== -1 || body.indexOf('完成拼图') !== -1) {"
    "    return { detected: true, reason: 'geetest_body' };"
    "  }"
    "  return { detected: false };"
    "}"
)

_PLATFORM_CAPTCHA_DOM_JS: dict[str, str] = {
    "csdn": _CSDN_CF_DETECT_JS,
    "zhihu": _ZHIHU_CAPTCHA_DETECT_JS,
    "jianshu": _GEETEST_CAPTCHA_DETECT_JS,
    "juejin": _GEETEST_CAPTCHA_DETECT_JS,
    "baijiahao": _BAIJIAHAO_CAPTCHA_DETECT_JS,
    "baidu": _BAIJIAHAO_CAPTCHA_DETECT_JS,
}


def get_platform_captcha_dom_js(platform: str) -> str | None:
    """获取平台专用的验证码 DOM 检测 JS（无注册则返回 None）。"""
    return _PLATFORM_CAPTCHA_DOM_JS.get((platform or "").lower())


def detect_captcha_url(url: str) -> CaptchaDetection:
    """基于 URL 检测验证码/风控页。"""
    lowered = (url or "").lower()
    for kw in _CAPTCHA_URL_KEYWORDS:
        if kw in lowered:
            return CaptchaDetection(True, f"url_{kw}", url)
    if "bind" in lowered and ("phone" in lowered or "mobile" in lowered):
        return CaptchaDetection(True, "phone_bind_url", url)
    return CaptchaDetection(False, "", url)


def _detect_captcha_via_dom(page: Any, dom_detect_js: str) -> CaptchaDetection:
    try:
        result = page.evaluate(dom_detect_js)
        if isinstance(result, dict):
            detected = bool(result.get("detected", False))
            reason = result.get("reason", "dom_detected") if detected else ""
            return CaptchaDetection(detected, reason, "")
        if result:
            return CaptchaDetection(True, "dom_detected", "")
        return CaptchaDetection(False, "", "")
    except Exception:
        return CaptchaDetection(False, "dom_detect_error", "")


def detect_captcha_page(page: Any, dom_detect_js: str | None = None) -> CaptchaDetection:
    """检测当前页面是否处于验证码/风控状态。

    先检查 URL（快速路径），未命中再用平台 DOM 检测兜底。
    """
    try:
        url = page.evaluate("() => window.location.href")
    except Exception:
        if dom_detect_js:
            return _detect_captcha_via_dom(page, dom_detect_js)
        return CaptchaDetection(False)
    url_result = detect_captcha_url(url)
    if url_result.detected:
        return url_result
    if dom_detect_js:
        return _detect_captcha_via_dom(page, dom_detect_js)
    return url_result


def wait_until_captcha_resolved(
    page: Any,
    *,
    timeout_seconds: int | None = None,
    poll_seconds: int | None = None,
    dom_detect_js: str | None = None,
) -> bool:
    """轮询等待验证码页面消失（用户手动完成验证）。True=已消失。"""
    from .timeouts import CAPTCHA_POLL_SECONDS, CAPTCHA_WAIT_SECONDS

    if timeout_seconds is None:
        timeout_seconds = CAPTCHA_WAIT_SECONDS
    if poll_seconds is None:
        poll_seconds = CAPTCHA_POLL_SECONDS
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not detect_captcha_page(page, dom_detect_js=dom_detect_js).detected:
            return True
        time.sleep(poll_seconds)
    return False
