"""http_signed 规格的离线测试（不联网）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from anti_crawl_kit.http_signed import (
    SignedEndpoint,
    classify_signed_response,
    get_signer,
    list_signers,
    load_http_signed_spec,
    register_signer,
    render_params,
    spec_from_dict,
)

ROOT = Path(__file__).resolve().parents[1]


def _spec_data() -> dict:
    return {
        "name": "demo",
        "platform": "bilibili",
        "auth_check": "cookie:SESSDATA",
        "fallback": ["http_signed", "browser_xhr"],
        "endpoints": {
            "relation": {
                "url": "https://api.bilibili.com/x/relation/stat",
                "params": {"vmid": "{uid}"},
                "headers": {"Referer": "https://space.bilibili.com/{uid}"},
                "risk_codes": {"-352": "captcha", "-799": "rate_limited"},
                "required_fields": ["data.follower", "data.following"],
            }
        },
    }


def test_spec_from_dict_parses_endpoints() -> None:
    spec = spec_from_dict(_spec_data())
    assert spec.name == "demo"
    assert spec.platform == "bilibili"
    assert spec.auth_check == "cookie:SESSDATA"
    assert spec.fallback == ("http_signed", "browser_xhr")
    ep = spec.endpoint("relation")
    assert ep.method == "GET"
    assert ep.risk_codes["-352"] == "captcha"
    assert ep.required_fields == ("data.follower", "data.following")


def test_spec_to_dict_roundtrip() -> None:
    spec = spec_from_dict(_spec_data())
    data = spec.to_dict()
    assert data["endpoints"]["relation"]["params"] == {"vmid": "{uid}"}
    assert data["endpoints"]["relation"]["required_fields"] == ["data.follower", "data.following"]


def test_render_params_replaces_placeholders() -> None:
    ep = SignedEndpoint(
        name="relation",
        url="https://example.com",
        params={"vmid": "{uid}", "fixed": "1"},
    )
    assert render_params(ep, {"uid": "2"}) == {"vmid": "2", "fixed": "1"}


def test_render_params_keeps_unknown_placeholder() -> None:
    ep = SignedEndpoint(name="x", url="https://example.com", params={"a": "{missing}"})
    assert render_params(ep, {}) == {"a": "{missing}"}


def test_classify_http_status() -> None:
    ep = SignedEndpoint(name="x", url="https://example.com")
    assert classify_signed_response(0, None, ep) == "network"
    assert classify_signed_response(401, None, ep) == "login_required"
    assert classify_signed_response(403, None, ep) == "login_required"
    assert classify_signed_response(412, None, ep) == "captcha"
    assert classify_signed_response(429, None, ep) == "rate_limited"
    assert classify_signed_response(500, None, ep) == "network"


def test_classify_platform_risk_codes() -> None:
    ep = SignedEndpoint(
        name="x",
        url="https://example.com",
        risk_codes={"-352": "captcha", "-799": "rate_limited", "-509": "rate_limited"},
    )
    assert classify_signed_response(200, {"code": -352}, ep) == "captcha"
    assert classify_signed_response(200, {"code": -799}, ep) == "rate_limited"
    assert classify_signed_response(200, {"result": -509}, ep) == "rate_limited"


def test_classify_required_fields_missing_is_empty() -> None:
    ep = SignedEndpoint(
        name="x",
        url="https://example.com",
        required_fields=("data.follower",),
    )
    assert classify_signed_response(200, {"code": 0, "data": {}}, ep) == "empty"
    assert classify_signed_response(200, {"code": 0, "data": {"follower": 1}}, ep) == "ok"


def test_classify_empty_body() -> None:
    ep = SignedEndpoint(name="x", url="https://example.com")
    assert classify_signed_response(200, None, ep, text="") == "empty"
    assert classify_signed_response(200, None, ep, text="<html>") == "ok"


def test_register_and_get_signer() -> None:
    @register_signer("demo_signer")
    def _sign(ctx):
        return {"params": {"sig": "abc"}}

    assert "demo_signer" in list_signers()
    assert get_signer("demo_signer")({}) == {"params": {"sig": "abc"}}


def test_get_unknown_signer_raises() -> None:
    with pytest.raises(KeyError, match="不内置签名算法"):
        get_signer("not_registered")


def test_load_bilibili_yaml_example() -> None:
    pytest.importorskip("yaml", reason="需要 [config] extra")
    path = ROOT / "examples" / "platforms" / "bilibili_http_signed.yaml"
    spec = load_http_signed_spec(path)
    assert spec.platform == "bilibili"
    assert set(spec.endpoints) == {"relation", "navnum"}
    assert spec.endpoint("relation").required_fields == ("data.follower", "data.following")
    assert spec.endpoint("navnum").required_fields == ("data.video",)
