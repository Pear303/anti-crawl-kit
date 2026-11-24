"""验证墙探测脚本的模板替换。

这层以前用 `%` 格式化，漏替换会当场抛 TypeError；改成 `.replace()` 后
不会。这里锁住「替换完整」和「关键词确实进去了」两件事。
"""

from __future__ import annotations

import pytest

from anti_crawl_kit import challenge


def test_dom_probe_js_has_no_placeholders() -> None:
    js = challenge._DOM_PROBE_JS
    for placeholder in (
        "__SELECTORS__",
        "__TITLE_CAPTCHA__",
        "__TITLE_BLOCKED__",
        "__BODY_CAPTCHA__",
        "__BODY_BLOCKED__",
    ):
        assert placeholder not in js, f"占位符未替换: {placeholder}"


def test_dom_probe_js_contains_real_keywords() -> None:
    js = challenge._DOM_PROBE_JS

    # 选择器与关键词都以 JSON 字面量注入
    assert "#cf-challenge-running" in js
    assert "安全验证" in js
    assert "document.querySelectorAll" in js
    # 探测结果结构不能丢
    assert "detected: true" in js


def test_build_dom_probe_js_rejects_leftover_placeholder(monkeypatch) -> None:
    monkeypatch.setattr(
        challenge,
        "_DOM_PROBE_JS_TEMPLATE",
        challenge._DOM_PROBE_JS_TEMPLATE + "\nvar extra = __NOT_REPLACED__;",
    )

    with pytest.raises(RuntimeError, match="占位符"):
        challenge._build_dom_probe_js()
