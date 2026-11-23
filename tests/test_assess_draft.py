"""评估配置生成（assess_draft）的离线测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from anti_crawl_kit.assess_draft import (
    draft_assessment,
    pick_fields,
    pick_item_selector,
    write_draft,
)

PROBE = {
    "ok": True,
    "url": "https://books.toscrape.com/",
    "title": "All products",
    "item_candidates": [
        {
            "selector": "article.product_pod",
            "count": 20,
            "sample": "A Light in the Attic £51.77",
            "outline": [
                {"selector": "a", "text": "", "href": "catalogue/a.html"},
                {"selector": "h3", "text": "A Light in the Attic", "href": ""},
                {
                    "selector": "a",
                    "text": "A Light in the Attic",
                    "href": "catalogue/a.html",
                },
                {"selector": "p.price_color", "text": "£51.77", "href": ""},
                {"selector": "p.availability", "text": "In stock", "href": ""},
            ],
        },
        {"selector": "div.product_price", "count": 20, "sample": "£51.77", "outline": []},
        {"selector": "div.image_container", "count": 20, "sample": "", "outline": []},
    ],
}


def test_pick_item_selector_prefers_card_like() -> None:
    assert pick_item_selector(PROBE) == "article.product_pod"


def test_pick_fields_dedupes_parent_child_text() -> None:
    fields = pick_fields(PROBE, "article.product_pod")
    names = [f["name"] for f in fields]
    assert names == ["title", "url", "price", "stock"]
    by_name = {f["name"]: f for f in fields}
    assert by_name["title"]["selector"] == "h3"
    assert by_name["url"]["attr"] == "href"
    assert by_name["price"]["selector"] == "p.price_color"


def test_draft_assessment_yaml_is_loadable(tmp_path: Path) -> None:
    pytest.importorskip("yaml", reason="解析评估配置需要 [config] extra")
    from anti_crawl_kit.assess import load_assessment_config

    result = draft_assessment(PROBE)
    config_path = tmp_path / "books_assessment.yaml"
    config_path.write_text(result["config_yaml"], encoding="utf-8")

    config = load_assessment_config(config_path)
    assert config.platform == "books"
    assert config.item_selector == "article.product_pod"
    assert config.field_selectors["price"]["selector"] == "p.price_color"
    assert config.contract is not None
    assert config.contract.min_items == 10
    assert result["warnings"] == []


def test_write_draft_writes_both_files(tmp_path: Path) -> None:
    result = draft_assessment(PROBE, link_audit=True)
    paths = write_draft(result, tmp_path)

    config_file = Path(paths["config_path"])
    audit_file = Path(paths["audit_path"])
    assert config_file.exists() and audit_file.exists()
    assert audit_file.name in config_file.read_text(encoding="utf-8")


def test_draft_assessment_requires_url() -> None:
    with pytest.raises(ValueError, match="url"):
        draft_assessment({"item_candidates": []})


def test_draft_assessment_without_candidates_warns() -> None:
    result = draft_assessment({"url": "https://example.com/x"})
    assert result["item_selector"] == ""
    assert result["warnings"]
    assert "fields: {}" in result["config_yaml"]
