"""无浏览器测试：SSR 状态提取。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.state_extract import extract_balanced, extract_state, pick_path


def test_extract_balanced_simple():
    text = 'x = {"a": 1}; y = 2'
    raw = extract_balanced(text, text.index("{"))
    assert raw == '{"a": 1}'


def test_extract_balanced_nested():
    text = '{"a": {"b": [1, 2, {"c": 3}]}}'
    assert extract_balanced(text, 0) == text


def test_extract_balanced_string_with_brace():
    text = '{"a": "值里有 } 和 { 还有 \\" 引号"}'
    assert extract_balanced(text, 0) == text


def test_extract_balanced_unclosed():
    assert extract_balanced('{"a": 1', 0) is None
    assert extract_balanced("no brace", 0) is None


def test_pick_path_dot():
    data = {"a": {"b": {"c": 1}}}
    assert pick_path(data, "a.b.c") == 1


def test_pick_path_index_bracket():
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert pick_path(data, "a.b[1].c") == 2


def test_pick_path_index_dot():
    data = {"a": {"b": [10, 20]}}
    assert pick_path(data, "a.b.1") == 20


def test_pick_path_missing():
    assert pick_path({"a": 1}, "a.b") is None
    assert pick_path(None, "a") is None
    assert pick_path({"a": [1]}, "a[5]") is None
    assert pick_path({"a": [1]}, "a.name") is None


def test_pick_path_empty():
    assert pick_path({"a": 1}, "") == {"a": 1}


def test_extract_next_data():
    html = (
        "<html><body><div id='root'></div>"
        "<script id='__NEXT_DATA__' type='application/json'>"
        '{"props":{"pageProps":{"items":[{"id":1},{"id":2}]}}}'
        "</script></body></html>"
    )
    result = extract_state(html)
    assert result.ok is True
    assert result.key == "next_data"
    assert len(result.data["props"]["pageProps"]["items"]) == 2


def test_extract_initial_state():
    html = (
        "<html><body><script>"
        'window.__INITIAL_STATE__ = {"note":{"noteId":"abc","title":"标题"}};'
        "</script></body></html>"
    )
    result = extract_state(html)
    assert result.ok is True
    assert result.key == "initial_state"
    assert result.data["note"]["noteId"] == "abc"


def test_extract_nuxt():
    html = (
        "<html><body><script>"
        'window.__NUXT__={"data":[{"id":1,"title":"文章"}]};'
        "</script></body></html>"
    )
    result = extract_state(html)
    assert result.ok is True
    assert result.key == "nuxt"
    assert result.data["data"][0]["title"] == "文章"


def test_extract_with_undefined():
    html = (
        "<html><body><script>"
        'window.__INITIAL_STATE__ = {"a": undefined, "b": 1};'
        "</script></body></html>"
    )
    result = extract_state(html)
    assert result.ok is True
    assert result.data == {"a": None, "b": 1}


def test_extract_undefined_inside_string_not_replaced():
    html = (
        "<html><body><script>"
        'window.__INITIAL_STATE__ = {"text": "undefined is a word"};'
        "</script></body></html>"
    )
    result = extract_state(html)
    assert result.ok is True
    assert result.data["text"] == "undefined is a word"


def test_no_marker():
    result = extract_state("<html><body>plain page</body></html>")
    assert result.ok is False
    assert result.error == "no_state_marker"


def test_invalid_json():
    html = (
        "<html><body><script>"
        "window.__INITIAL_STATE__ = {not json};"
        "</script></body></html>"
    )
    result = extract_state(html)
    assert result.ok is False
    assert result.key == "initial_state"
    assert result.error.startswith("invalid_json")


def test_iife_nuxt_returns_false():
    html = (
        "<html><body><script>"
        "window.__NUXT__=(function(a){return {x:a}})(1);"
        "</script></body></html>"
    )
    result = extract_state(html)
    assert result.ok is False
    assert result.key == "nuxt"
    assert result.error == "js_expression"
