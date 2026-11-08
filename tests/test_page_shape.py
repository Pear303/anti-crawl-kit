"""无浏览器测试：页面形态分类。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.page_shape import classify_page_shape, detect_states

LONG_TEXT = "这是一段足够长的正文内容，用来模拟服务端渲染好的页面。" * 40


def test_ssr_page():
    html = f"<html><body><article>{LONG_TEXT}</article></body></html>"
    shape = classify_page_shape(html)
    assert shape.kind == "ssr"
    assert shape.visible_chars > 800
    assert shape.advice


def test_next_data():
    html = (
        "<html><body><div id='root'></div>"
        "<script id='__NEXT_DATA__' type='application/json'>{}</script>"
        "</body></html>"
    )
    shape = classify_page_shape(html)
    assert shape.kind == "ssr_state"
    assert "next_data" in shape.states


def test_next_rsc():
    html = (
        "<html><body><script>self.__next_f.push([1,\"data\"])</script>"
        f"<div>{LONG_TEXT}</div></body></html>"
    )
    shape = classify_page_shape(html)
    assert shape.kind == "ssr_state"
    assert "next_rsc" in shape.states


def test_nuxt():
    html = "<html><body><script>window.__NUXT__={}</script></body></html>"
    shape = classify_page_shape(html)
    assert shape.kind == "ssr_state"
    assert "nuxt" in shape.states


def test_detect_states_multiple():
    html = "<script>window.__INITIAL_STATE__={}</script><script>__NUXT__</script>"
    states = detect_states(html)
    assert "initial_state" in states
    assert "nuxt" in states


def test_spa_shell():
    padding = "<!-- " + "x" * 1500 + " -->"
    html = (
        "<html><head>"
        + "<script src='/a.js'></script>" * 5
        + "</head><body><div id='app'></div>"
        + padding
        + "</body></html>"
    )
    shape = classify_page_shape(html)
    assert shape.kind == "spa_shell"
    assert shape.visible_chars < 200
    assert shape.script_count == 5


def test_login_wall():
    html = "<html><body><div>请登录后查看完整内容</div></body></html>"
    shape = classify_page_shape(html)
    assert shape.kind == "login_wall"


def test_captcha_wall():
    html = "<html><body><div>请完成验证后继续访问</div></body></html>"
    shape = classify_page_shape(html)
    assert shape.kind == "captcha_wall"


def test_blocked_status():
    shape = classify_page_shape("<html></html>", status=403)
    assert shape.kind == "blocked"
    assert "status:403" in shape.signals


def test_tiny_response():
    shape = classify_page_shape("<html></html>", status=200)
    assert shape.kind == "tiny"


def test_normal_login_popup_not_captcha():
    html = (
        "<html><body>"
        + "<div>验证码登录 密码登录 获取短信验证码</div>"
        + f"<article>{LONG_TEXT}</article>"
        + "</body></html>"
    )
    shape = classify_page_shape(html)
    assert shape.kind == "ssr"


def test_unknown_shape():
    text = "中等长度内容。" * 100
    padding = "<!-- " + "x" * 600 + " -->"
    html = f"<html><body><div>{text}</div>{padding}</body></html>"
    shape = classify_page_shape(html)
    assert shape.kind in {"unknown", "ssr"}
    assert shape.advice
