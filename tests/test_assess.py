"""G0~G4 评估汇总测试（离线，用替身 fetch）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anti_crawl_kit.assess import (
    AssessmentConfig,
    assess,
    load_assessment_config,
)
from anti_crawl_kit.data_contract import DataContract

BOOKS_HTML = (
    "<html><head><title>Books</title></head><body>"
    + "<p>" + "正文内容" * 800 + "</p>"
    + "".join(
        f'<article class="product_pod"><h3><a title="Book {i}">Book {i}</a></h3>'
        f'<p class="price_color">£{i}.99</p></article>'
        for i in range(20)
    )
    + "</body></html>"
)


def _contract() -> DataContract:
    return DataContract.from_dict(
        {
            "name": "books",
            "id_field": "title",
            "min_items": 20,
            "fields": {
                "title": {"type": "string", "required": True, "min_len": 2},
                "price": {"type": "string", "required": True, "pattern": "^£"},
            },
        }
    )


def _config(**overrides) -> AssessmentConfig:
    data = dict(
        platform="books",
        url="https://books.toscrape.com/",
        item_selector="article.product_pod",
        field_selectors={
            "title": {"selector": "h3 a", "attr": "title"},
            "price": {"selector": "p.price_color"},
        },
        contract=_contract(),
        runs=3,
        concurrency=1,
        interval_ms=0,
    )
    data.update(overrides)
    return AssessmentConfig(**data)


def _fetch(url: str):
    return 200, BOOKS_HTML, ""


def test_assess_conditional_go() -> None:
    report = assess(_config(), fetch=_fetch)
    statuses = {gate.gate: gate.status for gate in report.gates}
    assert statuses == {
        "G0": "PASS",
        "G1": "SKIP",
        "G2": "PASS",
        "G3": "PASS",
        "G4": "WARN",
    }
    assert report.decision == "conditional_go"
    assert report.score > 0
    assert report.gaps


def test_assess_no_go_on_wrong_selector() -> None:
    report = assess(_config(item_selector="article.missing"), fetch=_fetch)
    statuses = {gate.gate: gate.status for gate in report.gates}
    assert statuses["G2"] == "FAIL"
    assert statuses["G3"] == "FAIL"
    assert report.decision == "no_go"


def test_assess_g2_skip_without_contract() -> None:
    report = assess(_config(contract=None), fetch=_fetch)
    g2 = next(gate for gate in report.gates if gate.gate == "G2")
    assert g2.status == "SKIP"
    assert any("数据契约" in gap for gap in report.gaps)


def test_assess_write(tmp_path: Path) -> None:
    report = assess(_config(), fetch=_fetch)
    json_path, md_path = report.write(tmp_path / "out")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["decision"] == "conditional_go"
    assert len(data["gates"]) == 5
    text = md_path.read_text(encoding="utf-8")
    assert "技术验证评估" in text
    assert "G2" in text


def test_config_from_dict() -> None:
    config = AssessmentConfig.from_dict(
        {
            "platform": "demo",
            "url": "https://example.com/",
            "contract": {"fields": {"title": {"type": "string"}}},
            "incremental": {"mode": "incremental", "min_new_items": 1},
            "runs": 5,
            "audit_only": ["anonymous_html"],
        }
    )
    assert config.runs == 5
    assert config.incremental.mode == "incremental"
    assert config.incremental.min_new_items == 1
    assert config.audit_only == ("anonymous_html",)


def test_load_config_yaml(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    path = tmp_path / "assess.yaml"
    path.write_text(
        "platform: demo\nurl: https://example.com/\nruns: 2\n",
        encoding="utf-8",
    )
    config = load_assessment_config(path)
    assert config.platform == "demo"
    assert config.runs == 2
