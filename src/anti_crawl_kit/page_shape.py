"""页面形态分类 —— 一眼判断「数据在哪、下一步走哪条路」。

接新平台时最容易犯的错，是拿一份 HTML 直接开始写选择器，
却没先看它到底是：

    - 服务端渲染好的完整页面（数据就在 HTML 里）
    - 注入了全局状态的 SSR（数据在 __NEXT_DATA__ / __INITIAL_STATE__）
    - 只有 <div id="app"></div> 的 SPA 壳（数据要等 JS）
    - 登录墙 / 验证码墙 / WAF 拦截页

形态判断错了，后面所有努力都是浪费。

用法::

    from anti_crawl_kit.page_shape import classify_page_shape

    shape = classify_page_shape(html, status=200)
    print(shape.kind, shape.advice)
    # ssr_state  数据在全局状态，HTTP 正则提取或浏览器读一次
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .risk import page_title, visible_text

# 常见 SSR 状态容器。2026 年 Next.js App Router 用 self.__next_f.push，
# 不再是单一 __NEXT_DATA__，所以单独列一项。
STATE_MARKERS: tuple[tuple[str, str], ...] = (
    ("next_data", "__NEXT_DATA__"),
    ("next_rsc", "self.__next_f.push"),
    ("nuxt", "__NUXT__"),
    ("initial_state", "__INITIAL_STATE__"),
    ("apollo", "__APOLLO_STATE__"),
    ("react_query", "__REACT_QUERY_STATE__"),
    ("redux", "__REDUX_STATE__"),
    ("preloaded_state", "__PRELOADED_STATE__"),
    ("remix", "__remixContext"),
    ("vue_app", "__vue_app__"),
)

_SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=[\"']", re.IGNORECASE)

_BLOCKED_STATUS = frozenset({401, 403, 429, 503, 521, 522})

_CAPTCHA_MARKERS = (
    "安全验证",
    "人机验证",
    "滑动验证",
    "拖动滑块",
    "请完成验证",
    "请输入验证码",
    "验证码校验",
)

_LOGIN_MARKERS = ("请登录", "请先登录", "登录后查看", "登录后才能", "扫码登录")

_ADVICE = {
    "ssr": "数据在 HTML，直接解析，不要开浏览器。",
    "ssr_state": "数据在全局状态：HTTP 正则提取，或浏览器打开一次读变量。",
    "spa_shell": "HTML 是空壳：先 js_api_hunt 找接口，再考虑浏览器拦截。",
    "login_wall": "需要登录：持久化 profile + 人工登录一次，之后复用。",
    "captcha_wall": "验证墙：硬停 + 截图 + 通知，绝不重试。",
    "blocked": "被拦截：换代理 / 降频 / 停，不要在同一 IP 上硬刷。",
    "tiny": "响应过短：先确认网络、DNS 或挑战页，不要急着写选择器。",
    "unknown": "形态不明：用 agent_probe 深挖，或直接看页面截图。",
}


@dataclass(frozen=True)
class PageShape:
    """一次页面形态判断的结果。"""

    kind: str
    advice: str
    signals: list[str] = field(default_factory=list)
    visible_chars: int = 0
    script_count: int = 0
    states: list[str] = field(default_factory=list)
    title: str = ""


def detect_states(html: str) -> list[str]:
    """返回命中的 SSR 状态容器名。"""
    body = html or ""
    return [name for name, marker in STATE_MARKERS if marker in body]


def classify_page_shape(html: str, *, status: int = 200) -> PageShape:
    """判断页面形态，并给出下一步建议。

    Args:
        html: 响应正文。
        status: HTTP 状态码（默认 200）。
    """
    body = html or ""
    size = len(body)
    visible = visible_text(body)
    visible_chars = len(visible)
    title = page_title(body)
    script_count = len(_SCRIPT_SRC_RE.findall(body))
    states = detect_states(body)
    signals: list[str] = []

    def make(kind: str) -> PageShape:
        signals.append(f"bytes:{size}")
        signals.append(f"visible:{visible_chars}")
        signals.append(f"scripts:{script_count}")
        return PageShape(
            kind=kind,
            advice=_ADVICE.get(kind, _ADVICE["unknown"]),
            signals=signals,
            visible_chars=visible_chars,
            script_count=script_count,
            states=states,
            title=title[:100],
        )

    if status in _BLOCKED_STATUS:
        signals.append(f"status:{status}")
        return make("blocked")

    lowered_title = title.lower()
    if any(marker in visible for marker in _CAPTCHA_MARKERS) or "captcha" in lowered_title:
        signals.append("captcha_marker")
        return make("captcha_wall")

    if visible_chars < 500 and any(marker in visible for marker in _LOGIN_MARKERS):
        signals.append("login_marker")
        return make("login_wall")

    # 状态容器是强信号，优先于「响应太短」。
    # Next.js / Nuxt 的首屏 HTML 可能只有几百字节，但数据已经在里面。
    if states:
        signals.append("state:" + ",".join(states))
        return make("ssr_state")

    if size < 1000:
        signals.append("tiny_body")
        return make("tiny")

    if visible_chars < 200 and script_count >= 3:
        signals.append("empty_visible_with_scripts")
        return make("spa_shell")

    if visible_chars > 800:
        return make("ssr")

    signals.append("no_clear_signal")
    return make("unknown")
