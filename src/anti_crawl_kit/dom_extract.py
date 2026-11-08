"""极简 DOM 抽取 —— 给数据契约校验提供 items（零第三方依赖）。

只支持最简单的选择器：
    - ``tag``
    - ``.class``
    - ``tag.class``
    - ``tag.class1.class2``
    - 后代组合：``h3 a`` / ``article.product_pod h3``

不支持 ``>``、``+``、``~``、属性选择器；需要这些时用浏览器侧
``querySelectorAll``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        node = Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs})
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].text_parts.append(data)


def parse_html(html: str) -> Node:
    builder = _Builder()
    builder.feed(html or "")
    return builder.root


_TOKEN_RE = re.compile(r"^[a-zA-Z][\w-]*|[.#][\w-]+")


def _tokens(selector: str) -> list[str]:
    return _TOKEN_RE.findall((selector or "").strip())


def _matches(node: Node, tokens: Iterable[str]) -> bool:
    for token in tokens:
        if token.startswith("."):
            if token[1:] not in node.attrs.get("class", "").split():
                return False
        elif token.startswith("#"):
            if node.attrs.get("id") != token[1:]:
                return False
        else:
            if node.tag != token.lower():
                return False
    return True


def walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from walk(child)


def select_all(root: Node, selector: str) -> list[Node]:
    """支持 ``tag`` / ``.class`` / ``tag.class`` 和后代组合（``h3 a``）。"""
    parts = [part for part in (selector or "").strip().split() if part]
    if not parts:
        return []
    current: list[Node] = [root]
    for part in parts:
        tokens = _tokens(part)
        if not tokens:
            return []
        matched: list[Node] = []
        seen: set[int] = set()
        for parent in current:
            for node in walk(parent):
                if node is parent or id(node) in seen:
                    continue
                if _matches(node, tokens):
                    matched.append(node)
                    seen.add(id(node))
        current = matched
        if not current:
            break
    return current


def node_text(node: Node | None) -> str:
    if node is None:
        return ""
    parts = list(node.text_parts)
    for child in node.children:
        parts.append(node_text(child))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def node_attr(node: Node | None, name: str) -> str:
    if node is None:
        return ""
    return node.attrs.get(name.lower(), "")


def count_matches(html: str, selector: str) -> int:
    return len(select_all(parse_html(html), selector))


def extract_records(
    html: str,
    card_selector: str,
    fields: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """按卡片 + 字段选择器抽取记录。

    ``fields`` 形如::

        {
            "title": {"selector": "h3", "attr": "title"},
            "price": {"selector": "p.price_color"},
            "tags":  {"selector": ".tag", "all": True},
        }
    """
    root = parse_html(html)
    records: list[dict[str, Any]] = []
    for card in select_all(root, card_selector):
        row: dict[str, Any] = {}
        for name, spec in fields.items():
            selector = str(spec.get("selector", ""))
            nodes = select_all(card, selector) if selector else [card]
            if spec.get("all"):
                if spec.get("attr"):
                    row[name] = [node_attr(node, str(spec["attr"])) for node in nodes]
                else:
                    row[name] = [node_text(node) for node in nodes]
                continue
            node = nodes[0] if nodes else None
            if spec.get("attr"):
                row[name] = node_attr(node, str(spec["attr"]))
            else:
                row[name] = node_text(node)
        records.append(row)
    return records


__all__ = [
    "Node",
    "count_matches",
    "extract_records",
    "node_attr",
    "node_text",
    "parse_html",
    "select_all",
    "walk",
]
