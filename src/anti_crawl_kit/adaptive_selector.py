"""自适应选择器 —— 页面改版后重新定位元素。

算法参考 Scrapling 的 adaptive scraping（BSD-3-Clause）：
https://github.com/D4Vinci/Scrapling

这里不引入 Scrapling 的依赖（lxml/orjson/tld），只用标准库重写核心思路：

    1. 保存元素特征：tag、text、attributes、path、parent、siblings；
    2. 改版后，遍历新页面所有元素，逐项算相似度；
    3. 返回得分最高且超过阈值的元素。

适用：
    - 平台改版，主选择器 0 命中；
    - 想加一层「兜底选择器」之外的自愈能力。

不适用：
    - 页面完全重写（结构、文案、层级全变）；
    - 需要精确到「第 N 个元素」的场景（相似元素会混淆）。

用法::

    from anti_crawl_kit.adaptive_selector import extract_features, relocate

    saved = extract_features(old_html, ".product")[0]
    result = relocate(new_html, saved, threshold=40)
    if result.ok:
        print(result.score, result.element.text())

许可证：本文件为独立实现，算法思路来自 Scrapling（BSD-3-Clause）。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

_TEXT_LIMIT = 300


@dataclass
class ElementFeatures:
    """一个元素的可比较特征。"""

    tag: str
    text: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    path: str = ""
    parent_name: str = ""
    parent_attribs: dict[str, str] = field(default_factory=dict)
    parent_text: str = ""
    siblings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "text": self.text,
            "attributes": self.attributes,
            "path": self.path,
            "parent_name": self.parent_name,
            "parent_attribs": self.parent_attribs,
            "parent_text": self.parent_text,
            "siblings": self.siblings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ElementFeatures":
        return cls(
            tag=str(data.get("tag") or ""),
            text=str(data.get("text") or ""),
            attributes=dict(data.get("attributes") or {}),
            path=str(data.get("path") or ""),
            parent_name=str(data.get("parent_name") or ""),
            parent_attribs=dict(data.get("parent_attribs") or {}),
            parent_text=str(data.get("parent_text") or ""),
            siblings=list(data.get("siblings") or []),
        )


@dataclass
class RelocateResult:
    """重定位结果。"""

    ok: bool
    score: float
    element: Any | None = None
    candidates: int = 0
    # 由命中元素推导出的候选 CSS 选择器（按优先级）
    selectors: list[str] = field(default_factory=list)


@dataclass
class AdaptiveProfile:
    """一份可持久化的页面基线。

    保存「改版前」的卡片选择器、数量和样本特征；
    改版后 ``relocate`` 用样本特征找回元素。
    """

    name: str
    url: str
    selector: str
    count: int
    samples: list[ElementFeatures]
    saved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "selector": self.selector,
            "count": self.count,
            "samples": [s.to_dict() for s in self.samples],
            "saved_at": self.saved_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AdaptiveProfile":
        return cls(
            name=str(data.get("name") or ""),
            url=str(data.get("url") or ""),
            selector=str(data.get("selector") or ""),
            count=int(data.get("count") or 0),
            samples=[
                ElementFeatures.from_dict(item)
                for item in (data.get("samples") or [])
                if isinstance(item, dict)
            ],
            saved_at=str(data.get("saved_at") or ""),
        )


def save_profile(path: str | Path, profile: AdaptiveProfile) -> Path:
    """把基线写到 JSON（自动建目录）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(target)
    return target


def load_profile(path: str | Path) -> AdaptiveProfile | None:
    """读取基线；文件不存在或损坏时返回 None（不抛异常）。"""
    target = Path(path)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    profile = AdaptiveProfile.from_dict(data)
    if not profile.samples:
        return None
    return profile


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent", "texts")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "_Node | None"):
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node] = []
        self.parent = parent
        self.texts: list[str] = []

    def text(self, limit: int = _TEXT_LIMIT) -> str:
        parts = list(self.texts)
        for child in self.children:
            parts.append(child.text(limit=limit))
        return re.sub(r"\s+", " ", " ".join(parts)).strip()[:limit]


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {}, None)
        self.stack: list[_Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.stack[-1].texts.append(text)


def _parse(html: str) -> _Node:
    parser = _TreeParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # 畸形 HTML：返回已解析部分，不抛异常
        pass
    return parser.root


def _iter_nodes(root: _Node) -> Iterable[_Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.tag != "#document":
            yield node
        stack.extend(reversed(node.children))


def _match_simple(node: _Node, selector: str) -> bool:
    """只支持 #id / .class / tag.class / tag 四种简单选择器。"""
    sel = (selector or "").strip()
    if not sel:
        return False
    classes = (node.attrs.get("class") or "").split()
    if sel.startswith("#"):
        return node.attrs.get("id") == sel[1:]
    if sel.startswith("."):
        return sel[1:] in classes
    if "." in sel:
        tag, cls = sel.split(".", 1)
        return node.tag == tag and cls in classes
    return node.tag == sel


def _features(node: _Node) -> ElementFeatures:
    path: list[str] = []
    current: _Node | None = node
    while current and current.tag != "#document":
        path.append(current.tag)
        current = current.parent
    path.reverse()

    parent = node.parent
    siblings = (
        [child.tag for child in parent.children if child is not node]
        if parent is not None
        else []
    )
    has_parent = parent is not None and parent.tag != "#document"

    return ElementFeatures(
        tag=node.tag,
        text=node.text(),
        attributes=dict(node.attrs),
        path=" > ".join(path),
        parent_name=parent.tag if has_parent else "",
        parent_attribs=dict(parent.attrs) if has_parent else {},
        parent_text=parent.text() if has_parent else "",
        siblings=siblings,
    )


_SAFE_NAME_RE = re.compile(r"^[A-Za-z_][\w-]*$")


def _safe_classes(node: "_Node") -> list[str]:
    raw = node.attrs.get("class") or ""
    return [c for c in raw.split() if _SAFE_NAME_RE.match(c)]


def selectors_for(element: Any) -> list[str]:
    """从 ``relocate`` 命中的元素推导候选 CSS 选择器。

    顺序：``#id`` → ``tag.class`` → ``.class`` → ``parent > tag`` → ``tag``。
    调用方应逐个用 ``querySelectorAll`` 验证命中数，选最接近原数量的一项。
    """
    node = element
    if not isinstance(node, _Node):
        return []

    out: list[str] = []
    tag = node.tag
    node_id = node.attrs.get("id") or ""
    if node_id and _SAFE_NAME_RE.match(node_id):
        out.append(f"#{node_id}")

    classes = _safe_classes(node)
    for cls in classes:
        out.append(f"{tag}.{cls}")
    for cls in classes:
        out.append(f".{cls}")

    parent = node.parent
    if parent is not None and parent.tag != "#document":
        parent_classes = _safe_classes(parent)
        for pcls in parent_classes[:1]:
            out.append(f"{parent.tag}.{pcls} > {tag}")
        out.append(f"{parent.tag} > {tag}")

    out.append(tag)

    seen: set[str] = set()
    return [s for s in out if not (s in seen or seen.add(s))]


def _dict_diff(left: dict[str, str], right: dict[str, str]) -> float:
    keys = SequenceMatcher(
        None, tuple(left.keys()), tuple(right.keys())
    ).ratio()
    values = SequenceMatcher(
        None, tuple(left.values()), tuple(right.values())
    ).ratio()
    return keys * 0.5 + values * 0.5


def similarity(original: ElementFeatures, candidate: ElementFeatures) -> float:
    """计算两个元素的相似度（0~100）。

    计分项和 Scrapling 一致：
        tag、text、attributes、class/id/href/src、path、parent、siblings。
    每项权重相同，最后取平均。
    """
    score = 0.0
    checks = 0

    score += 1.0 if original.tag == candidate.tag else 0.0
    checks += 1

    if original.text:
        score += SequenceMatcher(None, original.text, candidate.text).ratio()
        checks += 1

    score += _dict_diff(original.attributes, candidate.attributes)
    checks += 1

    for key in ("class", "id", "href", "src"):
        if original.attributes.get(key):
            score += SequenceMatcher(
                None,
                original.attributes[key],
                candidate.attributes.get(key, ""),
            ).ratio()
            checks += 1

    score += SequenceMatcher(None, original.path, candidate.path).ratio()
    checks += 1

    if original.parent_name and candidate.parent_name:
        score += SequenceMatcher(
            None, original.parent_name, candidate.parent_name
        ).ratio()
        checks += 1
        score += _dict_diff(
            original.parent_attribs, candidate.parent_attribs
        )
        checks += 1
        if original.parent_text:
            score += SequenceMatcher(
                None, original.parent_text, candidate.parent_text
            ).ratio()
            checks += 1

    if original.siblings:
        score += SequenceMatcher(
            None, original.siblings, candidate.siblings
        ).ratio()
        checks += 1

    if checks == 0:
        return 0.0
    return round((score / checks) * 100, 2)


def extract_features(
    html: str,
    selector: str,
    *,
    limit: int = 5,
) -> list[ElementFeatures]:
    """从 HTML 里提取匹配 selector 的元素特征。"""
    root = _parse(html)
    results: list[ElementFeatures] = []
    for node in _iter_nodes(root):
        if _match_simple(node, selector):
            results.append(_features(node))
            if len(results) >= limit:
                break
    return results


def relocate(
    html: str,
    saved: ElementFeatures,
    *,
    threshold: float = 40.0,
) -> RelocateResult:
    """在新页面里重新定位最相似的元素。

    Args:
        html: 改版后的页面。
        saved: 改版前保存的特征。
        threshold: 最低相似度（0~100），默认 40，和 Scrapling 一致。
    """
    root = _parse(html)
    best_node: _Node | None = None
    best_score = 0.0
    count = 0

    for node in _iter_nodes(root):
        count += 1
        score = similarity(saved, _features(node))
        if score > best_score:
            best_score = score
            best_node = node

    return RelocateResult(
        ok=best_node is not None and best_score >= threshold,
        score=best_score,
        element=best_node,
        candidates=count,
        selectors=selectors_for(best_node) if best_node is not None else [],
    )
