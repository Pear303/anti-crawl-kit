"""dom_probe 的离线测试：用 fake page，不开浏览器。"""

from __future__ import annotations

from typing import Any

from anti_crawl_kit.tools.dom_probe import probe_dom

CANDIDATES = [
    {"selector": "article.item", "count": 6, "sample": "标题 1"},
    {"selector": "div.row", "count": 6, "sample": "标题 1 标题 2"},
]
OUTLINES = {
    "article.item": [
        {"selector": "h2", "text": "标题 1", "href": "", "src": ""},
        {"selector": "a", "text": "标题 1", "href": "/p/1", "src": ""},
        {"selector": "span.price", "text": "¥19.9", "href": "", "src": ""},
    ],
    "div.row": [],
}


class FakePage:
    def __init__(self) -> None:
        self._url = ""

    @property
    def url(self) -> str:
        return self._url

    def title(self) -> str:
        return "Fixture 列表"

    def goto(self, url: str, timeout: int = 0) -> None:
        self._url = url

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def evaluate(self, script: str, arg: Any = None, **kwargs: Any) -> Any:
        if arg is not None:
            return [
                {"selector": selector, "outline": OUTLINES.get(selector, [])}
                for selector in arg
            ]
        if "counts" in script:
            return list(CANDIDATES)
        return []


def test_probe_dom_returns_candidates_and_outline() -> None:
    page = FakePage()
    payload = probe_dom("https://example.com/list", page=page, wait=0)

    assert payload["ok"] is True
    assert payload["url"] == "https://example.com/list"
    assert payload["dom_candidates"][0]["selector"] == "article.item"

    first = payload["item_candidates"][0]
    assert first["selector"] == "article.item"
    assert [node["selector"] for node in first["outline"]] == ["h2", "a", "span.price"]


def test_probe_dom_respects_max_candidates() -> None:
    payload = probe_dom("https://example.com/", page=FakePage(), max_candidates=1)
    assert len(payload["dom_candidates"]) == 1
    assert len(payload["item_candidates"]) == 1
