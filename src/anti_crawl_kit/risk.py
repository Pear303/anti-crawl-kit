"""HTTP 响应风险分类 —— 判断是否触发反爬/风控/需要降级浏览器。

关键教训（2026-11 实测）：

    1. 不能用「全文包含 captcha」判断验证码页。
       知乎 / 小红书的正常首页 JS bundle 里就有 captcha 字样，会误报。
    2. 不能只看状态码。
       微博未登录返回 200 + 47 字节 JSON（``{"ok":-100,...login.php"}``），
       按「200 即成功」会漏判。

所以分类顺序改成：状态码 → 短 JSON 登录墙 → URL → 可见文本 / 标题。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from ._compat import StrEnum


class RiskKind(StrEnum):
    OK = "ok"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA = "captcha"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    SIGNATURE_FAILED = "signature_failed"
    NETWORK_ERROR = "network_error"


@dataclass(frozen=True)
class RiskDecision:
    kind: RiskKind
    reason: str = ""
    # 是否应降级到真实浏览器执行（HTTP 直连被拦截时的标准动作）
    should_fallback_to_browser: bool = False


_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# 短 JSON 登录墙的典型特征（微博、部分 App 接口）
_LOGIN_JSON_MARKERS = (
    '"ok":-100',
    '"code":-1',
    '"code":-101',
    '"code":-100',
    "login.php",
    "请先登录",
    "未登录",
)

# 中文验证码页的可见文本特征。
# 注意：不要用裸「验证码」——知乎首页的「验证码登录」「获取短信验证码」
# 是正常登录弹窗，不是验证码墙。
_CAPTCHA_TEXT_MARKERS = (
    "安全验证",
    "人机验证",
    "滑动验证",
    "拖动滑块",
    "请完成验证",
    "请输入验证码",
    "验证码校验",
    "完成下方验证",
)

_LOGIN_TEXT_MARKERS = ("请登录", "请先登录", "登录后查看", "登录后才能")


def visible_text(html: str, limit: int = 4000) -> str:
    """去掉 script/style/标签，只留可见文本（截断，避免大页面开销）。"""
    text = _SCRIPT_RE.sub(" ", html or "")
    text = _STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def page_title(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    return re.sub(r"\s+", " ", match.group(1)).strip()[:200] if match else ""


# 兼容旧调用
_visible_text = visible_text
_title = page_title


def classify_http_result(status_code: int, text: str, url: str = "") -> RiskDecision:
    """按状态码 + 响应形态 + URL + 可见文本分类风险。

    使用顺序：先跑 HTTP 直连，命中 should_fallback_to_browser 就切浏览器。
    """
    body = text or ""
    lower_text = body.lower()
    lower_url = (url or "").lower()
    stripped = body.strip()

    if status_code == 429:
        return RiskDecision(RiskKind.RATE_LIMITED, "http_429", True)
    if status_code in {401, 403}:
        return RiskDecision(RiskKind.BLOCKED, f"http_{status_code}", True)
    if status_code >= 500:
        return RiskDecision(RiskKind.NETWORK_ERROR, f"http_{status_code}", True)

    # 202 + 空 body：起点等站点用「Accepted 但无内容」做反爬挑战。
    # 正常异步接口会返回 JSON，所以排除 { 开头的 body。
    if status_code == 202 and len(stripped) < 1000 and not stripped.startswith("{"):
        return RiskDecision(RiskKind.BLOCKED, "challenge_202", True)

    # 短 JSON 登录墙：状态码 200，但 body 是「请登录」协议
    if len(stripped) < 800 and stripped.startswith("{"):
        if any(marker in lower_text for marker in _LOGIN_JSON_MARKERS):
            return RiskDecision(RiskKind.LOGIN_REQUIRED, "login_json", True)

    # URL 命中验证码 / 挑战路径
    if any(k in lower_url for k in ("captcha", "verify", "challenge", "security-check")):
        return RiskDecision(RiskKind.CAPTCHA, "captcha_url", True)

    # 可见文本 / 标题：只在「页面本体」里判断，避免 JS bundle 误报
    visible = visible_text(body)
    if any(marker in visible for marker in _CAPTCHA_TEXT_MARKERS):
        return RiskDecision(RiskKind.CAPTCHA, "captcha_visible", True)

    title = page_title(body).lower()
    if "captcha" in title or "just a moment" in title or "attention required" in title:
        return RiskDecision(RiskKind.CAPTCHA, "captcha_title", True)

    if len(visible) < 500 and any(
        marker in visible for marker in _LOGIN_TEXT_MARKERS
    ):
        return RiskDecision(RiskKind.LOGIN_REQUIRED, "login_visible", True)
    if "signin" in lower_url or "login" in lower_url:
        return RiskDecision(RiskKind.LOGIN_REQUIRED, "login_url", True)

    # 签名失败只在「短响应」里判断。
    # 正常页面的 JS bundle 里也有 signature / a_bogus / w_rid，
    # 用全文匹配会把番茄、抖音这类正常首页误判成签名失效。
    if len(stripped) < 2000 and (
        "signature error" in lower_text
        or "invalid signature" in lower_text
        or "签名错误" in body
        or "a_bogus" in lower_text
        or "w_rid" in lower_text
    ):
        return RiskDecision(RiskKind.SIGNATURE_FAILED, "signature_failed", True)

    return RiskDecision(RiskKind.OK, "ok", False)
