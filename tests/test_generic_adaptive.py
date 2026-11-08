"""无浏览器测试：GenericCollector 的自适应兜底决策。

用 FakePage 模拟 querySelectorAll 命中数，不启动浏览器。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.adaptive_selector import (
    AdaptiveProfile,
    extract_features,
    save_profile,
)
from anti_crawl_kit.generic import FieldSpec, GenericCollector, PlatformSpec

OLD_HTML = """
<div class="container">
  <section class="products">
    <article class="product" id="p1"><h3>Product 1</h3></article>
    <article class="product" id="p2"><h3>Product 2</h3></article>
  </section>
</div>
"""

NEW_HTML = """
<div class="new-container">
  <section class="products">
    <article class="product new-class" data-id="p1"><h3>Product 1</h3></article>
    <article class="product new-class" data-id="p2"><h3>Product 2</h3></article>
  </section>
</div>
"""


class FakePage:
    def __init__(self, counts: dict[str, int], html: str):
        self.counts = counts
        self._html = html
        self.waited: list[str] = []

    def evaluate(self, script, arg=None, **kwargs):
        return self.counts.get(arg, 0)

    def wait_for_selector(self, selector, timeout=0):
        self.waited.append(selector)
        return self.counts.get(selector, 0) > 0

    def content(self):
        return self._html


def _spec(tmp_path, **kwargs) -> PlatformSpec:
    defaults = dict(
        name="demo",
        start_url="https://example.com/",
        card_selector="article.product",
        fields={"title": FieldSpec("h3")},
        adaptive_store=str(tmp_path / "demo.json"),
    )
    defaults.update(kwargs)
    return PlatformSpec(**defaults)


def _write_profile(tmp_path, selector="article.product", count=2):
    samples = extract_features(OLD_HTML, selector, limit=2)
    save_profile(
        tmp_path / "demo.json",
        AdaptiveProfile(
            name="demo",
            url="https://example.com/",
            selector=selector,
            count=count,
            samples=samples,
        ),
    )


def test_primary_hit(tmp_path):
    spec = _spec(tmp_path)
    page = FakePage({"article.product": 2}, OLD_HTML)
    selector, mode = GenericCollector(spec)._resolve_card_selector(page)
    assert selector == "article.product"
    assert mode == "primary"
    assert page.waited == []


def test_no_baseline(tmp_path):
    spec = _spec(tmp_path)
    page = FakePage({"article.product": 0}, NEW_HTML)
    selector, mode = GenericCollector(spec)._resolve_card_selector(page)
    assert selector == "article.product"
    assert mode == "no_baseline"


def test_adaptive_relocate(tmp_path):
    _write_profile(tmp_path)
    spec = _spec(tmp_path)
    page = FakePage(
        {"article.product": 0, "article.new-class": 2, "article": 2}, NEW_HTML
    )
    selector, mode = GenericCollector(spec)._resolve_card_selector(page)
    assert mode == "adaptive"
    assert selector == "article.new-class"


def test_adaptive_miss_on_unrelated_page(tmp_path):
    _write_profile(tmp_path)
    spec = _spec(tmp_path)
    page = FakePage({"article.product": 0}, "<html><body><div>完全无关</div></body></html>")
    selector, mode = GenericCollector(spec)._resolve_card_selector(page)
    assert selector == "article.product"
    assert mode == "adaptive_miss"


def test_adaptive_disabled(tmp_path):
    _write_profile(tmp_path)
    spec = _spec(tmp_path, adaptive_fallback=False)
    page = FakePage({"article.product": 0}, NEW_HTML)
    selector, mode = GenericCollector(spec)._resolve_card_selector(page)
    assert selector == "article.product"
    assert mode == "primary"


def test_from_dict_reads_adaptive_options():
    spec = PlatformSpec.from_dict(
        {
            "name": "demo",
            "start_url": "https://example.com/",
            "card_selector": ".item",
            "fields": {"title": "h3"},
            "adaptive_fallback": False,
            "adaptive_store": "x/y.json",
            "adaptive_threshold": 55,
        }
    )
    assert spec.adaptive_fallback is False
    assert spec.adaptive_store == "x/y.json"
    assert spec.adaptive_threshold == 55.0
    assert spec.fields["title"].selector == "h3"
