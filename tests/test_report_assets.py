"""内嵌样式/脚本的回归防护。

CSS 从 report.py 迁到 ui_assets.py 后，7KB 样式不再被任何用例守护。
这里锁住几条核心规则：样式被截断、被误删或占位符没替换时会直接报错。
"""

from __future__ import annotations

from anti_crawl_kit.report import render_html, render_index
from anti_crawl_kit.ui_assets import INDEX_HTML, REPORT_CSS


def test_report_css_contains_core_rules() -> None:
    for needle in (
        "--ok: #1a7f37",
        ".meta {",
        ".section {",
        ".gate {",
        "prefers-color-scheme",
        "@media print",
    ):
        assert needle in REPORT_CSS, f"REPORT_CSS 缺少 {needle!r}"


def test_report_html_embeds_css_once() -> None:
    html = render_html({"decision": "go", "score": 90}, kind="assess", title="样式回归")

    assert html.count("<style>") == 1
    assert ".section {" in html
    assert "prefers-color-scheme" in html
    assert "{_CSS}" not in html
    assert "样式回归" in html


def test_index_html_contains_core_rules() -> None:
    for needle in (".side h2 {", ".handoff {", ".card {"):
        assert needle in INDEX_HTML, f"INDEX_HTML 缺少 {needle!r}"


def test_render_index_keeps_style() -> None:
    html = render_index(
        [
            {
                "path": "a.html",
                "title": "报告 A",
                "kind": "health",
                "verdict": "PASS",
                "tone": "ok",
                "finished_at": "2026-11-11",
            }
        ]
    )

    assert html.count("<style>") == 1
    assert "prefers-color-scheme" in html
    assert "{_CSS}" not in html
