"""无浏览器测试：自适应选择器（改版后重定位）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.adaptive_selector import (
    ElementFeatures,
    extract_features,
    relocate,
    similarity,
)

OLD_HTML = """
<div class="container">
  <section class="products">
    <article class="product" id="p1">
      <h3>Product 1</h3>
      <p class="description">Description 1</p>
    </article>
    <article class="product" id="p2">
      <h3>Product 2</h3>
      <p class="description">Description 2</p>
    </article>
  </section>
</div>
"""

NEW_HTML = """
<div class="new-container">
  <div class="product-wrapper">
    <section class="products">
      <article class="product new-class" data-id="p1">
        <div class="product-info">
          <h3>Product 1</h3>
          <p class="new-description">Description 1</p>
        </div>
      </article>
      <article class="product new-class" data-id="p2">
        <div class="product-info">
          <h3>Product 2</h3>
          <p class="new-description">Description 2</p>
        </div>
      </article>
    </section>
  </div>
</div>
"""


def test_extract_features():
    items = extract_features(OLD_HTML, "#p1")
    assert len(items) == 1
    features = items[0]
    assert features.tag == "article"
    assert "Product 1" in features.text
    assert features.attributes.get("id") == "p1"
    assert features.path.endswith("article")
    assert features.parent_name == "section"


def test_extract_multiple_by_class():
    items = extract_features(OLD_HTML, ".product")
    assert len(items) == 2


def test_similarity_identical():
    features = extract_features(OLD_HTML, "#p1")[0]
    assert similarity(features, features) == 100.0


def test_relocate_after_structure_change():
    saved = extract_features(OLD_HTML, "#p1")[0]
    result = relocate(NEW_HTML, saved, threshold=40)
    assert result.ok is True
    assert result.score >= 40
    assert "Product 1" in result.element.text()


def test_relocate_prefers_right_item():
    saved = extract_features(OLD_HTML, "#p2")[0]
    result = relocate(NEW_HTML, saved, threshold=40)
    assert result.ok is True
    assert "Product 2" in result.element.text()


def test_relocate_no_match():
    saved = extract_features(OLD_HTML, "#p1")[0]
    unrelated = "<html><body><div>完全无关的页面内容</div></body></html>"
    result = relocate(unrelated, saved, threshold=40)
    assert result.ok is False


def test_manual_features_roundtrip():
    features = ElementFeatures(
        tag="article",
        text="hello",
        attributes={"class": "item"},
        path="div > article",
    )
    assert features.to_dict()["tag"] == "article"
    assert features.to_dict()["attributes"] == {"class": "item"}
