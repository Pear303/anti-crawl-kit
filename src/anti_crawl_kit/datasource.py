"""数据源分层兜底 —— 把「API 主、HTML 兜底、浏览器保底」落成代码。

对应文档：`docs/19-数据源选型与兜底.md`

解决三个问题：
    1. 主路径失败时，该不该回退？回退到哪一层？
    2. 主路径成功时，怎么发现「返回 200 但内容是旧的」？
    3. 出事时，怎么不改代码就切到备用路径？

用法::

    from anti_crawl_kit.datasource import (
        DataSourcePolicy, fetch_with_fallback,
    )

    outcome = fetch_with_fallback(
        {
            "api": fetch_api,
            "html": fetch_html,
            "browser": fetch_browser,
        },
        policy=DataSourcePolicy(
            primary="api",
            fallbacks=("html",),
            cross_check=True,
        ),
    )

    print(outcome.source, len(outcome.items))
    if outcome.cross_check and outcome.cross_check["alarm"]:
        ...  # API 与 HTML 差异过大
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from ._compat import StrEnum
from typing import Any, Callable, Iterable, Sequence

# 这些失败回退没有意义：风控/限流时换一层大概率还是被拦
NO_FALLBACK_RISKS = frozenset({"blocked", "rate_limited", "captcha"})

# 这些失败可以回退
FALLBACK_RISKS = frozenset({"network", "not_found", "empty", "unknown"})

# 签名失败优先转浏览器，不要硬算
SIGNATURE_RISK = "signature_failed"


class StopReason(StrEnum):
    """一次分层取数为什么停下。

    和 `RiskKind` 对齐，额外补 ``ok`` / ``empty`` / ``no_fetcher`` / ``quality_failed``。
    写进日志和结果里，下游才知道该重试、换路径，还是告警。
    """

    OK = "ok"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"
    CAPTCHA = "captcha"
    LOGIN_REQUIRED = "login_required"
    SIGNATURE_FAILED = "signature_failed"
    NOT_FOUND = "not_found"
    NETWORK = "network"
    EMPTY = "empty"
    QUALITY_FAILED = "quality_failed"
    NO_FETCHER = "no_fetcher"
    UNKNOWN = "unknown"


# WAF / 盾的指纹。只用于「报告检测到什么」，不用于「自动过盾」。
_WAF_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "cloudflare",
        (
            "cf-chl",
            "cf_chl",
            "just a moment",
            "attention required",
            "cf-browser-verification",
            "__cf_chl_",
        ),
    ),
    ("datadome", ("datadome.co", "dd_cookie", "captcha-delivery.com")),
    ("akamai", ("akamai", "reference #18.", "reference&#35;18.")),
    ("perimeterx", ("perimeterx", "px-captcha", "_pxappid")),
    ("shape", ("shape security", "distil_r_captcha", "distil")),
    ("incapsula", ("incapsula", "_incap_ses")),
    ("aliyun", ("acw_sc__v2", "arg1=")),
    ("tencent", ("captcha.qq.com", "tcaptcha")),
)


def detect_waf(text: str) -> str:
    """从响应文本 / 异常消息里识别 WAF 类型，识别不出返回空串。"""
    lower = (text or "").lower()
    if not lower:
        return ""
    for name, markers in _WAF_MARKERS:
        if any(marker in lower for marker in markers):
            return name
    return ""


@dataclass(frozen=True)
class DataSourcePolicy:
    """数据源选型策略。"""

    primary: str = "api"
    fallbacks: tuple[str, ...] = ("html",)
    cross_check: bool = True
    cross_check_n: int = 5
    diff_threshold: float = 0.1
    mode_env: str = "SOURCE_MODE"


@dataclass
class FetchOutcome:
    """一次分层取数的结果。"""

    ok: bool
    source: str
    items: list[Any] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    cross_check: dict[str, Any] | None = None
    # 为什么停在这里：ok / blocked / captcha / empty / no_fetcher ...
    stop_reason: str = ""
    # 识别到的 WAF（cloudflare / datadome / ...），没有则为空
    detection: str = ""
    elapsed_ms: int = 0
    # SOURCE_MODE 强制值（空表示按策略顺序）
    mode: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source": self.source,
            "items": len(self.items),
            "stop_reason": self.stop_reason,
            "detection": self.detection,
            "elapsed_ms": self.elapsed_ms,
            "mode": self.mode,
            "attempts": self.attempts,
            "cross_check": self.cross_check,
        }

    def summary(self) -> str:
        head = (
            f"ok={self.ok} source={self.source} items={len(self.items)} "
            f"stop_reason={self.stop_reason or '-'}"
        )
        if self.detection:
            head += f" detection={self.detection}"
        if self.mode:
            head += f" mode={self.mode}"
        head += f" elapsed={self.elapsed_ms}ms"
        lines = [head]
        for attempt in self.attempts:
            line = (
                f"  [{attempt.get('source')}] ok={attempt.get('ok')} "
                f"risk={attempt.get('risk')} {attempt.get('elapsed_ms', 0)}ms"
            )
            if attempt.get("detection"):
                line += f" detection={attempt['detection']}"
            if attempt.get("error"):
                line += f" {attempt['error']}"
            lines.append(line)
        if self.cross_check:
            cross = self.cross_check
            lines.append(
                f"  双跑: {cross.get('source')} 对比 {cross.get('compared')} 条 "
                f"差异率={cross.get('ratio')} "
                f"{'[!] 告警' if cross.get('alarm') else '正常'}"
            )
        return "\n".join(lines)


def classify_exception(exc: BaseException) -> str:
    """把异常归到风险类型，决定是否回退。

    这是 `risk.RiskKind` 的「异常版」：`RiskKind` 判 HTTP 响应，
    这里判异常消息。
    """
    text = str(exc or "").lower()

    if any(k in text for k in ("captcha", "验证码", "安全验证", "人机验证")):
        return "captcha"
    if detect_waf(text):
        return "blocked"
    if any(k in text for k in ("429", "rate limit", "too many requests", "请求过于频繁")):
        return "rate_limited"
    if any(k in text for k in ("403", "blocked", "forbidden", "访问异常", "环境异常")):
        return "blocked"
    if any(k in text for k in ("401", "login", "请登录", "未登录")):
        return "login_required"
    if any(k in text for k in ("signature", "a_bogus", "w_rid", "x-s", "x-zse")):
        return SIGNATURE_RISK
    if any(k in text for k in ("404", "not found", "不存在")):
        return "not_found"
    if any(k in text for k in ("timeout", "timed out", "connection", "ssl", "dns", "超时")):
        return "network"
    return "unknown"


def resolve_source_mode(policy: DataSourcePolicy) -> str:
    """读取环境变量回滚开关。

    返回空字符串表示按 policy 顺序执行；
    返回 ``api`` / ``html`` / ``browser`` 表示只跑指定路径。
    """
    mode = (os.getenv(policy.mode_env, "") or "").strip().lower()
    return mode


def _ordered_sources(policy: DataSourcePolicy, mode: str) -> list[str]:
    if mode:
        return [mode]
    order: list[str] = []
    for name in (policy.primary, *policy.fallbacks):
        if name and name not in order:
            order.append(name)
    return order


def _pick_text(item: Any, keys: Sequence[str]) -> str:
    if isinstance(item, dict):
        for key in keys:
            value = item.get(key)
            if value:
                return str(value)
    return ""


def _normalize(value: str, limit: int = 100) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def compare_items(
    primary_items: Iterable[Any],
    fallback_items: Iterable[Any],
    *,
    limit: int = 5,
    keys: Sequence[str] = ("title", "author", "name", "url", "content", "body", "text"),
) -> dict[str, Any]:
    """对比两条路径的前 N 条，返回差异率。

    只比前 N 条，避免全量双跑把成本翻倍。
    """
    left = list(primary_items)[:limit]
    right = list(fallback_items)[:limit]
    compared = 0
    mismatches = 0
    details: list[dict[str, Any]] = []

    for index in range(min(len(left), len(right))):
        a = _normalize(_pick_text(left[index], keys))
        b = _normalize(_pick_text(right[index], keys))
        if not a or not b:
            continue
        compared += 1
        if a != b:
            mismatches += 1
            details.append(
                {"index": index, "primary": a[:60], "fallback": b[:60]}
            )

    ratio = (mismatches / compared) if compared else 0.0
    return {
        "compared": compared,
        "mismatches": mismatches,
        "ratio": round(ratio, 3),
        "details": details,
    }


def _run_cross_check(
    fetchers: dict[str, Callable[[], list[Any]]],
    policy: DataSourcePolicy,
    primary_items: list[Any],
) -> dict[str, Any] | None:
    """主路径成功后，跑一次兜底路径做对比。"""
    for name in policy.fallbacks:
        fetcher = fetchers.get(name)
        if fetcher is None:
            continue
        try:
            other = fetcher()
        except Exception as exc:  # 对比失败不影响主结果
            return {
                "source": name,
                "compared": 0,
                "mismatches": 0,
                "ratio": 0.0,
                "details": [],
                "alarm": False,
                "error": str(exc)[:160],
            }
        if not other:
            continue
        result = compare_items(
            primary_items, other, limit=policy.cross_check_n
        )
        result["source"] = name
        result["alarm"] = result["ratio"] > policy.diff_threshold
        return result
    return None


def fetch_with_fallback(
    fetchers: dict[str, Callable[[], list[Any]]],
    *,
    policy: DataSourcePolicy | None = None,
    classify: Callable[[BaseException], str] | None = None,
) -> FetchOutcome:
    """按策略顺序取数，失败时分类回退。

    Args:
        fetchers: ``{"api": fn, "html": fn, "browser": fn}``，每个返回 list。
        policy: 选型策略。
        classify: 异常分类函数，默认 ``classify_exception``。
    """
    policy = policy or DataSourcePolicy()
    classify = classify or classify_exception
    mode = resolve_source_mode(policy)
    order = _ordered_sources(policy, mode)
    attempts: list[dict[str, Any]] = []
    started = time.perf_counter()

    def _elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    def _finish(ok: bool, source: str, items: list[Any], reason: str,
                detection: str = "", cross: dict[str, Any] | None = None) -> FetchOutcome:
        return FetchOutcome(
            ok=ok,
            source=source,
            items=items,
            attempts=attempts,
            cross_check=cross,
            stop_reason=reason,
            detection=detection,
            elapsed_ms=_elapsed(),
            mode=mode,
        )

    index = 0
    while index < len(order):
        source = order[index]
        index += 1
        fetcher = fetchers.get(source)
        if fetcher is None:
            continue

        attempt_started = time.perf_counter()
        try:
            items = fetcher()
        except Exception as exc:
            risk = classify(exc)
            detection = detect_waf(str(exc))
            attempts.append(
                {
                    "source": source,
                    "ok": False,
                    "risk": risk,
                    "detection": detection,
                    "error": str(exc)[:200],
                    "elapsed_ms": int((time.perf_counter() - attempt_started) * 1000),
                }
            )

            if risk in NO_FALLBACK_RISKS:
                return _finish(False, source, [], risk, detection)

            if risk == SIGNATURE_RISK and "browser" in fetchers and "browser" not in order:
                # 签名失效：把浏览器插到当前位置后面，优先于 HTML
                order.insert(index, "browser")

            continue

        if not items:
            attempts.append(
                {
                    "source": source,
                    "ok": False,
                    "risk": "empty",
                    "detection": "",
                    "error": "empty result",
                    "elapsed_ms": int((time.perf_counter() - attempt_started) * 1000),
                }
            )
            continue

        attempts.append(
            {
                "source": source,
                "ok": True,
                "risk": "ok",
                "detection": "",
                "error": "",
                "elapsed_ms": int((time.perf_counter() - attempt_started) * 1000),
            }
        )
        cross = None
        if policy.cross_check and source == policy.primary:
            cross = _run_cross_check(fetchers, policy, list(items))
        return _finish(True, source, list(items), StopReason.OK, cross=cross)

    # 所有路径都没拿到数据：区分「没配 fetcher」和「跑过但都空」
    if not attempts:
        return _finish(False, "", [], StopReason.NO_FETCHER)
    last = attempts[-1]
    reason = StopReason.EMPTY if last.get("risk") == "empty" else str(last.get("risk") or StopReason.UNKNOWN)
    return _finish(
        False,
        str(last.get("source") or ""),
        [],
        reason,
        str(last.get("detection") or ""),
    )
