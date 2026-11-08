"""无浏览器测试：自适应选择器的基线持久化与选择器推导。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.adaptive_selector import (
    AdaptiveProfile,
    extract_features,
    load_profile,
    relocate,
    save_profile,
)

OLD_HTML = """
<div class="container">
  <section class="products">
    <article class="product" id="p1">
      <h3>Product 1</h3>
    </article>
    <article class="product" id="p2">
      <h3>Product 2</h3>
    </article>
  </section>
</div>
"""

NEW_HTML = """
<div class="new-container">
  <section class="products">
    <article class="product new-class" data-id="p1">
      <h3>Product 1</h3>
    </article>
    <article class="product new-class" data-id="p2">
      <h3>Product 2</h3>
    </article>
  </section>
</div>
"""


def test_relocate_returns_selector_candidates():
    saved = extract_features(OLD_HTML, "#p1")[0]
    result = relocate(NEW_HTML, saved, threshold=40)
    assert result.ok is True
    assert "article.product" in result.selectors
    assert ".product" in result.selectors


def test_profile_roundtrip(tmp_path):
    samples = extract_features(OLD_HTML, ".product", limit=2)
    profile = AdaptiveProfile(
        name="demo",
        url="https://example.com/",
        selector=".product",
        count=2,
        samples=samples,
    )
    path = tmp_path / "demo.json"
    save_profile(path, profile)

    loaded = load_profile(path)
    assert loaded is not None
    assert loaded.name == "demo"
    assert loaded.selector == ".product"
    assert loaded.count == 2
    assert len(loaded.samples) == 2
    assert loaded.samples[0].tag == "article"
    assert "Product 1" in loaded.samples[0].text


def test_load_profile_missing(tmp_path):
    assert load_profile(tmp_path / "nope.json") is None


def test_load_profile_corrupted(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_profile(path) is None


def test_load_profile_empty_samples(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text('{"name": "x", "samples": []}', encoding="utf-8")
    assert load_profile(path) is None


def test_profile_saved_at_auto_filled(tmp_path):
    samples = extract_features(OLD_HTML, ".product", limit=1)
    profile = AdaptiveProfile(
        name="demo", url="", selector=".product", count=1, samples=samples
    )
    path = tmp_path / "demo.json"
    save_profile(path, profile)
    loaded = load_profile(path)
    assert loaded is not None
    assert loaded.saved_at
