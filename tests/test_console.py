"""控制台编码兜底 + 脱敏测试。"""

from __future__ import annotations

import builtins
import io
import sys

import pytest

from anti_crawl_kit.console import (
    install_safe_print,
    is_safe_print_installed,
    redact_mapping,
    redact_secrets,
    safe_print,
    uninstall_safe_print,
)


def test_safe_print_survives_gbk_console(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="gbk", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    # 模拟快手标题里的不可编码字符，不能把主流程炸掉
    safe_print("标题", "测试😀内容")
    stream.flush()
    assert buffer.getvalue()


def test_install_safe_print_is_idempotent() -> None:
    original = builtins.print
    try:
        install_safe_print()
        first = builtins.print
        install_safe_print()
        assert builtins.print is first
        assert is_safe_print_installed() is True
    finally:
        uninstall_safe_print()
        assert builtins.print is original
        assert is_safe_print_installed() is False


def test_redact_cookie_header() -> None:
    raw = "Cookie: SESSDATA=abc123456; bili_jct=xyz987654"
    out = redact_secrets(raw)
    assert "abc123456" not in out
    assert "xyz987654" not in out
    assert "Cookie:" in out


def test_redact_authorization_bearer() -> None:
    out = redact_secrets("Authorization: Bearer abcdefghijklmn")
    assert "abcdefghijklmn" not in out
    assert "Authorization:" in out


def test_redact_kv_secret() -> None:
    out = redact_secrets("access_token=abcdef123456&page=1")
    assert "abcdef123456" not in out
    assert "page=1" in out


def test_short_value_not_redacted() -> None:
    assert redact_secrets("token=1") == "token=1"


def test_bare_cookie_name_not_redacted() -> None:
    assert redact_secrets("SESSDATA") == "SESSDATA"


def test_redact_mapping_recursive() -> None:
    data = {
        "auth_cookies": ["SESSDATA"],
        "password": "hunter2xx",
        "nested": {"access_token": "abcdef123456", "page": 1},
    }
    out = redact_mapping(data)
    assert out["auth_cookies"] == ["SESSDATA"]
    assert out["password"] == "<redacted>"
    assert out["nested"]["access_token"] == "<redacted>"
    assert out["nested"]["page"] == 1
