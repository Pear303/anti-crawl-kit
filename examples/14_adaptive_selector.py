"""示例 14：自适应选择器 —— 页面改版后重新定位元素。

算法参考 Scrapling 的 adaptive scraping（BSD-3-Clause），
本示例使用工作包内的零依赖实现。

场景：
    旧页面用 `#p1` 能选中商品；
    改版后 `id` 变成了 `data-id`，CSS 选择器失效；
    自适应选择器通过 tag/text/attributes/path/parent/siblings
    算相似度，把商品重新找回来。

运行::

    python examples/14_adaptive_selector.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.adaptive_selector import extract_features, relocate

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


def main() -> int:
    saved = extract_features(OLD_HTML, "#p1")[0]
    print("=== 改版前：保存特征 ===")
    print(f"tag={saved.tag}")
    print(f"text={saved.text}")
    print(f"path={saved.path}")
    print(f"attributes={saved.attributes}")

    still_there = extract_features(NEW_HTML, "#p1")
    print(f"\n=== 改版后：#p1 还能选中吗 ===\n{bool(still_there)}")

    result = relocate(NEW_HTML, saved, threshold=40)
    print("\n=== 自适应重定位 ===")
    print(
        f"ok={result.ok} score={result.score} "
        f"candidates={result.candidates}"
    )
    if result.ok:
        print(f"命中元素: <{result.element.tag}> {result.element.text()}")
    else:
        print("未找到足够相似的元素，建议提高阈值或改用兜底选择器")

    unrelated = "<html><body><div>完全无关的页面内容</div></body></html>"
    negative = relocate(unrelated, saved, threshold=40)
    print("\n=== 无关页面（应该拒绝）===")
    print(f"ok={negative.ok} score={negative.score}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
