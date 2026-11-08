"""无浏览器测试：空壳页检测。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.empty_shell import detect_empty_shell

NORMAL = (
    "<html><body><div class='item'>"
    + ("这是一段足够长的正文内容，用来测试空壳检测。" * 20)
    + "</div></body></html>"
)


def test_spa_empty_shell():
    html = '<html><body><div id="app"></div><script>var a=1</script></body></html>'
    result = detect_empty_shell(html)
    assert result.is_empty is True
    assert any(reason.startswith("body_too_short") for reason in result.reasons)
    assert result.body_chars < 50


def test_normal_page_not_empty():
    result = detect_empty_shell(NORMAL, selector=".item")
    assert result.is_empty is False
    assert result.matched == 1


def test_selector_miss():
    result = detect_empty_shell(NORMAL, selector=".note-item")
    assert result.is_empty is True
    assert "selector_miss:.note-item" in result.reasons
    assert result.matched == 0


def test_skeleton_marker():
    html = NORMAL + '<div class="skeleton"></div>'
    result = detect_empty_shell(html, selector=".item")
    assert result.is_empty is True
    assert any(reason.startswith("skeleton:") for reason in result.reasons)


def test_complex_selector_not_supported():
    result = detect_empty_shell(NORMAL, selector="div > .item")
    assert result.matched == -1
    assert "selector_miss:div > .item" not in result.reasons


def test_id_selector():
    html = '<html><body><div id="content">正文内容足够长，用于测试。</div></body></html>'
    result = detect_empty_shell(html, selector="#content")
    assert result.matched == 1


def test_tag_class_selector():
    result = detect_empty_shell(NORMAL, selector="div.item")
    assert result.matched == 1
