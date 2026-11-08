"""空壳页检测 —— 判断「HTML 拿到了，但数据没渲染」。

这是「证据驱动升级」最常用的信号：不是 API 报错，而是页面看起来正常，
目标节点却是空的。此时应该升级到下一层渲染，而不是重试同一层。

检测四类信号：

    1. 正文太短（SPA 首屏只有 <div id="app"></div>）
    2. 目标选择器 0 命中
    3. 骨架屏标记（skeleton / shimmer）
    4. 调用方自己补充的 SSR 空状态判断

用法::

    from anti_crawl_kit.empty_shell import detect_empty_shell

    result = detect_empty_shell(html, selector="section.note-item")
    if result.is_empty:
        print(result.reasons)
        # ['selector_miss:section.note-item']
        # → 升级到浏览器渲染
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SKELETON_MARKERS = ("skeleton", "shimmer")

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class EmptyShellResult:
    """空壳检测结果。"""

    is_empty: bool
    reasons: list[str]
    body_chars: int
    matched: int = -1


def _body_text(html: str) -> str:
    text = _SCRIPT_RE.sub(" ", html or "")
    text = _STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _count_selector(html: str, selector: str) -> int:
    """只支持简单的 id / class / tag.class 选择器。

    复杂选择器（含空格、>、[、:、逗号）返回 -1，表示「不支持判断」，
    而不是「0 命中」，避免误报。
    """
    sel = (selector or "").strip()
    if not sel:
        return -1
    if any(ch in sel for ch in (" ", ">", "[", ":", ",")):
        return -1

    source = html or ""
    if sel.startswith("#"):
        return len(
            re.findall(rf'id=["\']{re.escape(sel[1:])}["\']', source)
        )
    if sel.startswith("."):
        cls = re.escape(sel[1:])
        return len(
            re.findall(
                rf'class=["\'][^"\']*\b{cls}\b[^"\']*["\']', source
            )
        )
    if "." in sel:
        tag, cls = sel.split(".", 1)
        return len(
            re.findall(
                rf'<{re.escape(tag)}\b[^>]*class=["\'][^"\']*\b{re.escape(cls)}\b',
                source,
                re.IGNORECASE,
            )
        )
    return len(re.findall(rf"<{re.escape(sel)}\b", source, re.IGNORECASE))


def detect_empty_shell(
    html: str,
    *,
    selector: str = "",
    min_body_chars: int = 200,
) -> EmptyShellResult:
    """判断页面是否是空壳。

    Args:
        html: 页面 HTML。
        selector: 目标元素选择器（简单 id/class/tag.class）。
        min_body_chars: 正文低于此长度视为空壳。
    """
    body = _body_text(html)
    body_chars = len(body)
    matched = _count_selector(html, selector) if selector else -1

    reasons: list[str] = []
    if body_chars < min_body_chars:
        reasons.append(f"body_too_short:{body_chars}")
    if selector and matched == 0:
        reasons.append(f"selector_miss:{selector}")

    lowered = (html or "").lower()
    for marker in _SKELETON_MARKERS:
        if marker in lowered:
            reasons.append(f"skeleton:{marker}")
            break

    return EmptyShellResult(
        is_empty=bool(reasons),
        reasons=reasons,
        body_chars=body_chars,
        matched=matched,
    )
