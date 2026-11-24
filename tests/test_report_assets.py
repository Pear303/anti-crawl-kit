"""内嵌样式/脚本的回归防护。

CSS 从 report.py 迁到 ui_assets.py 后，7KB 样式不再被任何用例守护。
这里同时锁「关键规则」和「整体体量」：只查几个锚点的话，中段被删掉
20% 仍然会全绿。
"""

from __future__ import annotations

from anti_crawl_kit.report import render_html, render_index
from anti_crawl_kit.ui_assets import INDEX_HTML, REPORT_CSS

# 头部、中部、尾部的代表性规则，覆盖配色、布局、表格、筛选器
REPORT_ANCHORS = (
    "--ok: #1a7f37",
    ".meta {",
    ".section {",
    ".pill.tone-fail",
    ".cell-note",
    "td.mono",
    ".table-wrap",
    ".matrix",
    ".filters",
    ".gate {",
    "prefers-color-scheme",
    "@media print",
)


def test_report_css_contains_core_rules() -> None:
    for needle in REPORT_ANCHORS:
        assert needle in REPORT_CSS, f"REPORT_CSS 缺少 {needle!r}"


def test_report_css_is_not_truncated() -> None:
    # 迁移时是 7049 字节 / 82 条规则；留 6% 余量，删掉中段 20% 会被抓住
    assert len(REPORT_CSS) > 6600, f"REPORT_CSS 只有 {len(REPORT_CSS)} 字节"
    assert REPORT_CSS.count("{") >= 78, "REPORT_CSS 的规则条数明显减少"


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


def test_index_html_is_not_truncated() -> None:
    assert len(INDEX_HTML) > 18000, f"INDEX_HTML 只有 {len(INDEX_HTML)} 字节"
    assert INDEX_HTML.count("{") >= 135


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
