"""内容质量校验 —— 防「投毒」和低质数据。

对应文档：`docs/03-防风控策略清单.md` 第 4.5 节「内容层：投毒」

站点不一定拦你，它可能**污染你拿到的数据**：
    - API 返回 `polluted=true` 的毒章；
    - 正文混入广告、重复段落、乱码；
    - 用低熵填充冒充正文。

本模块提供纯本地的质量校验，不依赖网络。

用法::

    from anti_crawl_kit.content_quality import check_content

    result = check_content(text, polluted=raw.get("polluted"))
    if not result.ok:
        print(result.reasons)   # ['polluted: API 标记 polluted=true']
        # 整章丢弃，不重试
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

DEFAULT_AD_KEYWORDS: tuple[str, ...] = (
    "请记住本站",
    "最新章节",
    "手机阅读",
    "加入书签",
    "无弹窗",
    "百度搜索",
    "笔趣阁",
    "首发",
    "更新最快",
    "章节错误",
    "本站域名",
    "一秒记住",
    "免费阅读",
    "请收藏",
)


@dataclass(frozen=True)
class QualityIssue:
    """一条质量问题。"""

    kind: str
    detail: str
    penalty: float = 0.0


@dataclass
class QualityResult:
    """质量校验结果。"""

    ok: bool
    score: float
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [f"{issue.kind}: {issue.detail}" for issue in self.issues]


def _repeat_ratio(text: str) -> float:
    """重复行占比。只统计长度 >= 8 的行，避免短行误判。"""
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 8]
    if len(lines) < 4:
        return 0.0
    counts = Counter(lines)
    repeated = sum(count for count in counts.values() if count > 1)
    return repeated / len(lines)


def _unique_ratio(text: str) -> float:
    """字符种类占比。低熵文本（纯填充）会很低。"""
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return 0.0
    return len(set(compact)) / len(compact)


def check_content(
    text: str,
    *,
    polluted: bool | None = None,
    min_chars: int = 300,
    max_repeat_ratio: float = 0.35,
    min_unique_ratio: float = 0.08,
    max_bad_char_ratio: float = 0.01,
    ad_keywords: Iterable[str] = DEFAULT_AD_KEYWORDS,
    threshold: float = 0.6,
) -> QualityResult:
    """校验一段正文是否可信。

    Args:
        text: 正文。
        polluted: API 返回的投毒标记；``True`` 直接判失败，不重试。
        min_chars: 最短长度。
        max_repeat_ratio: 允许的重复行占比。
        min_unique_ratio: 最低字符种类占比。
        max_bad_char_ratio: 允许的 ``\\ufffd`` 替换字符占比。
        ad_keywords: 广告关键词。
        threshold: 总分低于此值判失败。

    Returns:
        ``QualityResult(ok, score, issues)``。
    """
    issues: list[QualityIssue] = []
    value = text or ""

    # 1. API 直接标记：最可靠，命中即失败
    if polluted is True:
        issues.append(
            QualityIssue("polluted", "API 标记 polluted=true", 1.0)
        )
        return QualityResult(False, 0.0, issues)

    length = len(value.strip())
    if length < min_chars:
        issues.append(
            QualityIssue("too_short", f"{length} 字 < {min_chars}", 0.6)
        )

    repeat_ratio = _repeat_ratio(value)
    if repeat_ratio > max_repeat_ratio:
        issues.append(
            QualityIssue(
                "repeated",
                f"重复行 {repeat_ratio:.0%} > {max_repeat_ratio:.0%}",
                0.6,
            )
        )

    hits = [keyword for keyword in ad_keywords if keyword in value]
    if hits:
        issues.append(
            QualityIssue(
                "ad_keywords",
                f"命中 {len(hits)} 个：{', '.join(hits[:3])}",
                0.4,
            )
        )

    bad_ratio = value.count("\ufffd") / max(1, len(value))
    if bad_ratio > max_bad_char_ratio:
        issues.append(
            QualityIssue("mojibake", f"替换字符 {bad_ratio:.1%}", 0.6)
        )

    unique_ratio = _unique_ratio(value)
    if length >= 50 and unique_ratio < min_unique_ratio:
        issues.append(
            QualityIssue(
                "low_entropy",
                f"字符种类 {unique_ratio:.1%} < {min_unique_ratio:.0%}",
                0.5,
            )
        )

    score = max(0.0, 1.0 - sum(issue.penalty for issue in issues))
    score = round(score, 2)
    return QualityResult(score >= threshold, score, issues)


def check_batch(
    items: Iterable[dict[str, Any]],
    *,
    text_key: str = "content",
    **kwargs: Any,
) -> list[QualityResult]:
    """批量校验，适合在落盘前过滤。"""
    return [
        check_content(str(item.get(text_key) or ""), **kwargs)
        for item in items
    ]
