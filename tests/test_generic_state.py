"""无浏览器测试：GenericCollector 的 SSR 状态优先。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.generic import FieldSpec, GenericCollector, PlatformSpec

STATE_HTML = """
<html><body>
<div id="app"></div>
<script>
window.__INITIAL_STATE__ = {
  "home": {
    "writerList": [
      {"uid": "1", "name": "作者A", "introduction": "简介A"},
      {"uid": "2", "name": "作者B", "introduction": "简介B"}
    ]
  }
};
</script>
</body></html>
"""

PLAIN_HTML = """
<html><body><article class="item"><h3>标题</h3></article></body></html>
"""


class FakePage:
    def __init__(self, html: str):
        self._html = html
        self.evaluate_calls = 0

    def content(self):
        return self._html

    def evaluate(self, script, arg=None, **kwargs):
        self.evaluate_calls += 1
        return 1


def _spec(**kwargs) -> PlatformSpec:
    defaults = dict(
        name="demo",
        start_url="https://example.com/",
        card_selector="article.item",
        fields={
            "uid": FieldSpec(state_path="uid"),
            "name": FieldSpec(state_path="name"),
        },
        state_path="home.writerList",
    )
    defaults.update(kwargs)
    return PlatformSpec(**defaults)


def test_state_hit():
    page = FakePage(STATE_HTML)
    rows = GenericCollector(_spec())._extract_from_state(page)
    assert len(rows) == 2
    assert rows[0] == {"uid": "1", "name": "作者A"}
    assert rows[1] == {"uid": "2", "name": "作者B"}
    # 状态命中时不应碰 DOM
    assert page.evaluate_calls == 0


def test_field_without_state_path_uses_field_name():
    spec = _spec(
        fields={
            "uid": FieldSpec(),
            "name": FieldSpec(),
        }
    )
    rows = GenericCollector(spec)._extract_from_state(FakePage(STATE_HTML))
    assert rows[0]["name"] == "作者A"


def test_nested_state_path():
    spec = _spec(
        state_path="home.writerList",
        fields={"name": FieldSpec(state_path="name")},
    )
    rows = GenericCollector(spec)._extract_from_state(FakePage(STATE_HTML))
    assert len(rows) == 2


def test_state_path_missing_falls_back():
    page = FakePage(STATE_HTML)
    rows = GenericCollector(_spec(state_path="home.notExist"))._extract_from_state(page)
    assert rows == []


def test_state_fields_all_empty_falls_back():
    spec = _spec(
        fields={
            "uid": FieldSpec(state_path="no_such_field"),
            "name": FieldSpec(state_path="also_missing"),
        }
    )
    rows = GenericCollector(spec)._extract_from_state(FakePage(STATE_HTML))
    assert rows == []


def test_state_disabled():
    page = FakePage(STATE_HTML)
    rows = GenericCollector(_spec(state_extract=False))._extract_from_state(page)
    assert rows == []


def test_no_state_path_returns_empty():
    page = FakePage(STATE_HTML)
    rows = GenericCollector(_spec(state_path=""))._extract_from_state(page)
    assert rows == []


def test_non_ssr_state_page_returns_empty():
    page = FakePage(PLAIN_HTML)
    rows = GenericCollector(_spec())._extract_from_state(page)
    assert rows == []


def test_numeric_list_survives_postprocess():
    spec = _spec(fields={"tags": FieldSpec(all=True)})
    cleaned = GenericCollector(spec)._postprocess([{"tags": [1, 2, 3]}])
    assert cleaned[0]["tags"] == [1, 2, 3]


def test_from_dict_reads_state_options():
    spec = PlatformSpec.from_dict(
        {
            "name": "demo",
            "start_url": "https://example.com/",
            "card_selector": ".item",
            "state_path": "props.pageProps.items",
            "state_extract": False,
            "fields": {
                "name": {"selector": "h3", "state_path": "title"},
            },
        }
    )
    assert spec.state_path == "props.pageProps.items"
    assert spec.state_extract is False
    assert spec.fields["name"].state_path == "title"
    assert spec.fields["name"].selector == "h3"
