"""从 SSR 页面提取全局状态 JSON —— `ssr_state` 形态的配套工具。

`page_shape` 只能判断「数据在 __NEXT_DATA__ / __NUXT__ / __INITIAL_STATE__ 里」，
真正要用还得把那段 JSON 取出来。本模块用标准库做「括号配对 + 字符串感知」，
不依赖 lxml / json5 / pyjsparser。

支持：

    - <script id="__NEXT_DATA__" type="application/json">{...}</script>
    - window.__INITIAL_STATE__ = {...}
    - window.__NUXT__ = {...}
    - window.__APOLLO_STATE__ = {...}
    - window.__PRELOADED_STATE__ = {...}
    - window.__REDUX_STATE__ = {...}

不支持的（返回 ok=False，交给浏览器读变量）：

    - Nuxt 2 的 IIFE 形式：window.__NUXT__=(function(a){...})(...)
    - Next.js App Router 的 RSC 流：self.__next_f.push([1,"..."]) 需要拼接多段

用法::

    from anti_crawl_kit.state_extract import extract_state

    result = extract_state(html)
    if result.ok:
        print(result.key, type(result.data))
        items = result.data["props"]["pageProps"]["items"]
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# 按「提取成功率」排序：script 标签最稳，window 赋值次之
_MARKERS: tuple[tuple[str, str], ...] = (
    ("next_data", "__NEXT_DATA__"),
    ("initial_state", "__INITIAL_STATE__"),
    ("nuxt", "__NUXT__"),
    ("apollo", "__APOLLO_STATE__"),
    ("preloaded_state", "__PRELOADED_STATE__"),
    ("redux", "__REDUX_STATE__"),
)

_NEXT_DATA_SCRIPT_RE = re.compile(
    r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

# 路径片段：key 或 key[0]
_PATH_PART_RE = re.compile(r"^([^\[\]]+)(?:\[(\d+)\])?$")

# JSON 里不合法的字面量（JS 对象常见）
_JS_LITERALS = (
    (re.compile(r"(?<![\w\"'])undefined(?![\w\"'])"), "null"),
    (re.compile(r"(?<![\w\"'])NaN(?![\w\"'])"), "null"),
    (re.compile(r"(?<![\w\"'])Infinity(?![\w\"'])"), "null"),
)


def pick_path(data: Any, path: str) -> Any:
    """按点号路径取值，支持 ``a.b`` / ``a.b[0].c`` / ``a.b.0.c``。

    取不到返回 None，不抛异常。
    """
    current = data
    for raw_part in (path or "").split("."):
        part = raw_part.strip()
        if not part:
            continue
        match = _PATH_PART_RE.match(part)
        if not match:
            return None
        key, index = match.group(1), match.group(2)

        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list):
            if not key.isdigit():
                return None
            position = int(key)
            current = current[position] if 0 <= position < len(current) else None
        else:
            return None

        if current is None:
            return None

        if index is not None:
            position = int(index)
            if isinstance(current, list) and 0 <= position < len(current):
                current = current[position]
            else:
                return None

    return current


@dataclass(frozen=True)
class StateResult:
    """一次状态提取的结果。"""

    ok: bool
    key: str = ""
    data: Any = None
    error: str = ""


def extract_balanced(text: str, start: int) -> str | None:
    """从 start（必须是 { 或 [）开始，返回括号配对的子串。

    会跳过字符串里的括号，处理反斜杠转义。
    找不到配对的右括号时返回 None。
    """
    if start < 0 or start >= len(text):
        return None
    opener = text[start]
    if opener not in "{[":
        return None
    closer = "}" if opener == "{" else "]"

    depth = 0
    in_string = False
    quote = ""
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue

        if char in ("'", '"'):
            in_string = True
            quote = char
            continue

        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def _clean_js_literals(raw: str) -> str:
    cleaned = raw
    for pattern, replacement in _JS_LITERALS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def _loads(raw: str) -> Any:
    return json.loads(_clean_js_literals(raw))


def _extract_next_data(html: str) -> str | None:
    match = _NEXT_DATA_SCRIPT_RE.search(html or "")
    return match.group(1).strip() if match else None


def _extract_assignment(html: str, marker: str) -> tuple[str, str] | None:
    """提取 `marker = {...}` 里的对象，同时返回 marker 到 `{` 之间的前缀。

    前缀用来判断这是不是 IIFE（`(function(...){...})(...)`）。
    """
    body = html or ""
    for match in re.finditer(re.escape(marker), body):
        tail = body[match.end() :]
        # 跳过空白和等号
        eq = re.search(r"[=:]\s*", tail)
        if not eq:
            continue
        start = match.end() + eq.end()
        # 找第一个 { 或 [
        brace = re.search(r"[\{\[]", body[start:])
        if not brace:
            continue
        raw = extract_balanced(body, start + brace.start())
        if raw:
            prefix = body[match.end() : start + brace.start()]
            return raw, prefix
    return None


def extract_state(html: str) -> StateResult:
    """按优先级尝试提取页面里的 SSR 状态 JSON。"""
    body = html or ""

    raw = _extract_next_data(body)
    if raw:
        try:
            return StateResult(True, "next_data", _loads(raw))
        except Exception as exc:
            return StateResult(False, "next_data", None, f"invalid_json:{exc!r}")

    for key, marker in _MARKERS[1:]:
        extracted = _extract_assignment(body, marker)
        if not extracted:
            continue
        raw, prefix = extracted
        # Nuxt 2 的 IIFE：window.__NUXT__=(function(a,b){...})(...)
        # 不是 JSON 坏了，是必须用浏览器求值。
        if "function(" in prefix or "=>" in prefix:
            return StateResult(False, key, None, "js_expression")
        try:
            return StateResult(True, key, _loads(raw))
        except Exception as exc:
            return StateResult(False, key, None, f"invalid_json:{exc!r}")

    return StateResult(False, "", None, "no_state_marker")
