"""极简 DOM 抽取测试。"""

from __future__ import annotations

from anti_crawl_kit.dom_extract import (
    count_matches,
    extract_records,
    node_attr,
    node_text,
    parse_html,
    select_all,
)

HTML = """
<html><body>
  <article class="product_pod">
    <h3><a title="Book A">Book A</a></h3>
    <p class="price_color">£10.00</p>
  </article>
  <article class="product_pod">
    <h3><a title="Book B">Book B</a></h3>
    <p class="price_color">£20.00</p>
  </article>
</body></html>
"""


def test_select_by_tag_class() -> None:
    root = parse_html(HTML)
    cards = select_all(root, "article.product_pod")
    assert len(cards) == 2
    assert count_matches(HTML, "article") == 2


def test_descendant_selector_and_attr() -> None:
    root = parse_html(HTML)
    links = select_all(root, "h3 a")
    assert len(links) == 2
    assert node_attr(links[0], "title") == "Book A"
    assert node_text(links[0]) == "Book A"


def test_extract_records() -> None:
    records = extract_records(
        HTML,
        "article.product_pod",
        {
            "title": {"selector": "h3 a", "attr": "title"},
            "price": {"selector": "p.price_color"},
        },
    )
    assert records == [
        {"title": "Book A", "price": "£10.00"},
        {"title": "Book B", "price": "£20.00"},
    ]


def test_extract_records_missing_field_is_empty() -> None:
    records = extract_records(
        HTML,
        "article.product_pod",
        {"missing": {"selector": ".nope"}},
    )
    assert records[0]["missing"] == ""


def test_extract_records_all() -> None:
    records = extract_records(
        HTML,
        "article.product_pod",
        {"links": {"selector": "a", "all": True, "attr": "title"}},
    )
    assert records[0]["links"] == ["Book A"]
