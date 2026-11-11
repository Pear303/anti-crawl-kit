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

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from ._compat import StrEnum
from typing import Any, Iterable, Mapping


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


# ═════════════════════════════════════════════════════════
# 单一来源校验：所有派生点必须与 PlatformCapability 一致
# ═════════════════════════════════════════════════════════
#
# 真实事故：新增 3 个平台时，能力表、采集器、发布器都加了，唯独
# runner.py 的入口提取漏分支 → 任务全空；同一次事故里还有 cookie 名单
# Python/JS 两处漂移（快手）→ 永远判未登录。
#
# 结论：平台信息只能有一个来源。任何派生点（regex / cookie 名单 /
# 选择器 / stealth 参数）都必须从这个 dataclass 派生，并用测试断言一致。

class SpecConsistencyError(ValueError):
    """派生点与平台 spec 不一致。"""


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, (tuple, list, set, frozenset)):
        return tuple(str(item) for item in value if item not in (None, ""))
    return (str(value),)


def derived_view(spec: PlatformCapability) -> dict[str, tuple[str, ...]]:
    """spec 的「派生点」规范视图。

    派生代码（入口提取 regex、cookie 名单、选择器、发布 URL）应该只读这里，
    不要再各自维护一份。
    """
    return {
        "auth_cookies": _as_tuple(spec.auth_cookies),
        "cookie_domain": _as_tuple(spec.cookie_domain),
        "creator_center_url": _as_tuple(spec.creator_center_url),
        "qrcode_selector": _as_tuple(spec.qrcode_selector),
        "collector_module": _as_tuple(spec.collector_module),
        "collector_class": _as_tuple(spec.collector_class),
        "entry_param": _as_tuple(spec.entry_param),
        "publisher_module": _as_tuple(spec.publisher_module),
        "publisher_class": _as_tuple(spec.publisher_class),
        "publish_url": _as_tuple(spec.publish_url),
        "video_publish_url": _as_tuple(spec.video_publish_url),
    }


_QRCODE_SELECTOR_TYPES = frozenset({"role", "css", "iframe_role"})


def validate_spec(spec: PlatformCapability) -> list[str]:
    """检查 spec 自身完整性；返回问题列表（空列表 = 通过）。"""
    issues: list[str] = []
    name = spec.platform or "<unnamed>"
    if not spec.platform:
        issues.append("platform 不能为空")
    if spec.supports_login and not spec.auth_cookies:
        issues.append(f"{name}: supports_login=True 但 auth_cookies 为空（登录判定会永远失败）")
    if spec.collector_module and not spec.collector_class:
        issues.append(f"{name}: 有 collector_module 但缺 collector_class")
    if spec.collector_class and not spec.collector_module:
        issues.append(f"{name}: 有 collector_class 但缺 collector_module")
    if spec.collector_module and not spec.entry_param:
        issues.append(f"{name}: 有采集器但缺 entry_param（新增平台时易漏入口分支）")
    if spec.needs_login and not spec.auth_cookies:
        issues.append(f"{name}: needs_login=True 但 auth_cookies 为空")
    if spec.publisher_module and not spec.publisher_class:
        issues.append(f"{name}: 有 publisher_module 但缺 publisher_class")
    if spec.publisher_class and not spec.publisher_module:
        issues.append(f"{name}: 有 publisher_class 但缺 publisher_module")
    if spec.supports_text_publish and not spec.publish_url:
        issues.append(f"{name}: supports_text_publish=True 但 publish_url 为空")
    if spec.supports_video_publish and not spec.video_publish_url:
        issues.append(f"{name}: supports_video_publish=True 但 video_publish_url 为空")
    if spec.qrcode_selector and spec.qrcode_selector_type not in _QRCODE_SELECTOR_TYPES:
        issues.append(
            f"{name}: qrcode_selector_type={spec.qrcode_selector_type!r} 不在 "
            f"{sorted(_QRCODE_SELECTOR_TYPES)}"
        )
    if not isinstance(spec.rate_limit, dict):
        issues.append(f"{name}: rate_limit 必须是 dict")
    return issues


def check_derived_consistency(
    spec: PlatformCapability,
    derived: Mapping[str, Any],
) -> list[str]:
    """比对派生点与 spec 是否一致。

    Args:
        derived: ``{字段名: 值或 {来源名: 值}}``。例如::

            {
                "auth_cookies": {
                    "python": ["SESSDATA"],
                    "javascript": ["SESSDATA"],
                },
                "entry_param": ["uid"],
            }

    值可以是单个字符串、列表或 tuple；列表比较不区分顺序。
    """
    issues: list[str] = []
    view = derived_view(spec)
    for field_name, sources in derived.items():
        if field_name not in view:
            issues.append(f"{spec.platform}: 未知派生字段 {field_name!r}")
            continue
        expected = set(view[field_name])
        source_map = sources if isinstance(sources, Mapping) else {"default": sources}
        for source_name, values in source_map.items():
            actual = set(_as_tuple(values))
            if actual != expected:
                missing = sorted(expected - actual)
                extra = sorted(actual - expected)
                issues.append(
                    f"{spec.platform}.{field_name}[{source_name}] 与 spec 不一致: "
                    f"缺 {missing} 多 {extra}"
                )
    return issues


@dataclass
class PlatformSpecRegistry:
    """平台 spec 注册表：拒绝重复，提供指纹与一致性断言。"""

    _specs: dict[str, PlatformCapability] = field(default_factory=dict)

    def __init__(self, specs: Iterable[PlatformCapability] = ()) -> None:
        self._specs = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: PlatformCapability) -> None:
        if not spec.platform:
            raise ValueError("platform 不能为空")
        if spec.platform in self._specs:
            raise ValueError(f"平台重复注册: {spec.platform}")
        self._specs[spec.platform] = spec

    def get(self, platform: str) -> PlatformCapability | None:
        return self._specs.get(platform)

    def __contains__(self, platform: object) -> bool:
        return str(platform) in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    def platforms(self) -> list[str]:
        return sorted(self._specs)

    def specs(self) -> list[PlatformCapability]:
        return [self._specs[name] for name in self.platforms()]

    def to_dicts(self) -> list[dict[str, Any]]:
        return [spec.to_dict() for spec in self.specs()]

    def validate(self) -> list[str]:
        """校验所有 spec 自身完整性。"""
        issues: list[str] = []
        for spec in self.specs():
            issues.extend(validate_spec(spec))
        return issues

    def consistency_issues(
        self,
        derived: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[str]:
        """校验所有派生点。

        ``derived`` 形如 ``{platform: {字段名: 值或 {来源名: 值}}}``；
        省略时只做 spec 自身校验。
        """
        issues: list[str] = list(self.validate())
        for platform, mapping in (derived or {}).items():
            spec = self.get(platform)
            if spec is None:
                issues.append(f"派生点引用了未注册平台: {platform}")
                continue
            issues.extend(check_derived_consistency(spec, mapping))
        return issues

    def assert_consistent(
        self,
        derived: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        issues = self.consistency_issues(derived)
        if issues:
            raise SpecConsistencyError("; ".join(issues))

    def fingerprint(self) -> str:
        """稳定指纹：平台 spec 任何字段变化都会变（用于快照测试）。"""
        payload = json.dumps(
            self.to_dicts(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "CHROME_EPOCH_OFFSET",
    "CookieState",
    "IdentityState",
    "PlatformCapability",
    "PlatformSpecRegistry",
    "SpecConsistencyError",
    "check_derived_consistency",
    "check_identity",
    "cookie_expired",
    "derived_view",
    "eval_cookie_state",
    "normalize_handle",
    "should_block_task",
    "validate_spec",
]
