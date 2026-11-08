"""平台能力描述 + 登录态三态校验。

解决两个高频问题：
    1. 接新平台时，信息散落在 N 个文件里 → 用 PlatformCapability 收敛。
    2. 「登录了」到底是有效、过期还是从未登录 → 用 CookieState 三态。

设计原则：
    - 不依赖浏览器也能做基础判定（传 cookie 列表即可）。
    - 任何异常都降级，不阻塞任务。
    - 身份比对只做规范化后的字符串比较，平台差异交给调用方。

用法::

    from anti_crawl_kit.platform_spec import (
        PlatformCapability, eval_cookie_state, check_identity,
    )

    state, reason = eval_cookie_state(cookies, ["SESSDATA"])
    if state is CookieState.EXPIRED:
        ...  # 提示重新登录

    if check_identity(actual_uid, expected_uid) == "mismatch":
        ...  # 用错账号，必须失败
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable


class CookieState(StrEnum):
    """登录态三态。不要用 bool。"""

    VALID = "valid"        # 有未过期的认证 cookie
    EXPIRED = "expired"    # 有认证 cookie，但已过期
    MISSING = "missing"    # 没有认证 cookie（从未登录）


class IdentityState(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"    # 缺少任一侧信息，无法判断


# Chrome/Chromium Cookies SQLite 的 WebKit 纪元（1601-01-01）与 Unix 纪元差。
CHROME_EPOCH_OFFSET = 11_644_473_600

_HANDLE_PREFIX_RE = re.compile(
    r"^(mid|uid|douyin_id|sec_uid|user_id|account_id|bili_mid)\s*:\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlatformCapability:
    """单个平台的完整能力描述。

    新增平台时只填这张表，不要让登录/采集/发布各维护一份。
    """

    platform: str
    label: str = ""

    # ── 登录 ──
    creator_center_url: str = ""
    supports_login: bool = False
    auth_cookies: tuple[str, ...] = ()
    cookie_domain: str = ""
    handle_source: dict[str, Any] = field(default_factory=dict)
    qrcode_selector: str = ""
    qrcode_selector_type: str = "role"   # role / css / iframe_role

    # ── 采集 ──
    collector_module: str = ""
    collector_class: str = ""
    entry_param: str = ""                # uid / user_id / sec_uid / url
    needs_login: bool = False
    known_unavailable: dict[str, str] = field(default_factory=dict)

    # ── 发布 ──
    publisher_module: str = ""
    publisher_class: str = ""
    supports_text_publish: bool = False
    publish_url: str = ""
    supports_video_publish: bool = False
    video_publish_url: str = ""

    # ── 频率 ──
    rate_limit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "label": self.label,
            "creator_center_url": self.creator_center_url,
            "supports_login": self.supports_login,
            "auth_cookies": list(self.auth_cookies),
            "cookie_domain": self.cookie_domain,
            "handle_source": self.handle_source,
            "qrcode_selector": self.qrcode_selector,
            "qrcode_selector_type": self.qrcode_selector_type,
            "collector_module": self.collector_module,
            "collector_class": self.collector_class,
            "entry_param": self.entry_param,
            "needs_login": self.needs_login,
            "known_unavailable": self.known_unavailable,
            "publisher_module": self.publisher_module,
            "publisher_class": self.publisher_class,
            "supports_text_publish": self.supports_text_publish,
            "publish_url": self.publish_url,
            "supports_video_publish": self.supports_video_publish,
            "video_publish_url": self.video_publish_url,
            "rate_limit": self.rate_limit,
        }


def cookie_expired(cookie: dict[str, Any], *, now: float | None = None) -> bool:
    """判断单个 cookie 是否过期。

    兼容两种来源：
        - Chrome SQLite：``expires_utc``（WebKit 微秒）
        - Playwright：``expires``（Unix 秒，-1/0 表示会话 cookie）

    缺字段时保守判「未过期」，避免误杀存量调用。
    """
    now = time.time() if now is None else now

    expires_utc = cookie.get("expires_utc")
    if expires_utc:
        try:
            return (float(expires_utc) / 1_000_000 - CHROME_EPOCH_OFFSET) < now
        except (TypeError, ValueError):
            pass

    expires = cookie.get("expires")
    if expires in (None, "", -1, 0, "-1", "0"):
        return False
    try:
        return float(expires) < now
    except (TypeError, ValueError):
        return False


def eval_cookie_state(
    cookies: Iterable[dict[str, Any]],
    auth_cookie_names: Iterable[str],
    *,
    now: float | None = None,
) -> tuple[CookieState, str]:
    """判定登录态三态，返回 ``(state, reason)``。

    Args:
        cookies: cookie 列表，每项至少含 ``name`` / ``value``；
                 可选 ``expires``（Unix 秒）或 ``expires_utc``（WebKit 微秒）。
        auth_cookie_names: 该平台的认证 cookie 名，如 ``("SESSDATA",)``。
        now: 测试注入的当前时间戳。

    Returns:
        (CookieState.VALID / EXPIRED / MISSING, 原因字符串)
    """
    names = {str(n) for n in auth_cookie_names if n}
    if not names:
        return CookieState.MISSING, "no_auth_names_configured"

    matched = [
        c
        for c in cookies
        if str(c.get("name", "")) in names and c.get("value")
    ]
    if not matched:
        return CookieState.MISSING, "no_matching_cookie"

    if all(cookie_expired(c, now=now) for c in matched):
        return CookieState.EXPIRED, "all_expired"
    return CookieState.VALID, "ok"


def normalize_handle(raw: str | None) -> str:
    """去掉 ``mid:`` / ``uid:`` / ``sec_uid:`` 等前缀，便于跨来源比对。"""
    text = (raw or "").strip()
    return _HANDLE_PREFIX_RE.sub("", text)


def check_identity(actual: str | None, expected: str | None) -> IdentityState:
    """比对「当前登录账号」与「期望账号」。

    - 任一侧为空 → UNKNOWN（不要误报 mismatch）。
    - 规范化后相等 → MATCH。
    """
    a = normalize_handle(actual)
    e = normalize_handle(expected)
    if not a or not e:
        return IdentityState.UNKNOWN
    return IdentityState.MATCH if a == e else IdentityState.MISMATCH


def should_block_task(
    state: CookieState,
    identity: IdentityState,
    *,
    is_publish: bool = False,
) -> tuple[bool, str]:
    """根据登录态 + 身份比对，决定是否阻断任务。

    规则（来自生产决策）：
        - 身份不符 → 一律阻断（发布/采集都会用错账号）。
        - 未登录/过期 → 采集放行（现场等登录）；发布阻断。
    """
    if identity is IdentityState.MISMATCH:
        return True, "登录态与所选账号不匹配，请为该账号重新登录"
    if state is CookieState.VALID:
        return False, ""
    if is_publish:
        return True, "登录态无效或已过期，发布任务需要先重新登录"
    return False, "登录态无效，采集任务将现场等待登录"
