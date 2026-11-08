"""path_audit 的离线测试（不联网、不启动浏览器）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from anti_crawl_kit.risk import RiskDecision, RiskKind
from anti_crawl_kit.tools import path_audit as audit


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        data=None,
        risk: RiskDecision | None = None,
    ):
        self.status_code = status_code
        self.text = text
        self.data = data
        self.headers: dict[str, str] = {}
        self.url = ""
        self.risk = risk or RiskDecision(RiskKind.OK)


class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    def get_json(self, url: str, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.response

    def post_json(self, url: str, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.response

    def get_text(self, url: str, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.response


def _endpoint(**kwargs) -> audit.AuditEndpoint:
    data = {
        "name": "relation",
        "url": "https://api.example.com/relation",
        "path": "anonymous_json",
        "fields": {"followers": "data.follower", "following": "data.following"},
        "required_fields": ("data.follower", "data.following"),
        "field_semantics": {
            "followers": "api.follower",
            "following": "api.following",
        },
    }
    data.update(kwargs)
    return audit.AuditEndpoint.from_dict(data)


def _config(**kwargs) -> audit.AuditConfig:
    data = {
        "platform": "demo",
        "test_url": "https://example.com/profile/1",
        "uid": "1",
        "field_semantics": {
            "followers": "api.follower",
            "following": "api.following",
        },
        "endpoints": [
            {
                "path": "anonymous_json",
                "name": "relation",
                "url": "https://api.example.com/relation",
                "params": {"vmid": "{uid}"},
                "fields": {
                    "followers": "data.follower",
                    "following": "data.following",
                },
                "required_fields": ["data.follower", "data.following"],
            }
        ],
    }
    data.update(kwargs)
    return audit.AuditConfig.from_dict(data)


def _spec(key: str) -> audit.PathSpec:
    return audit.PATHS[key]


# ── 路径目录 / 配置 ─────────────────────────────────


def test_path_catalog_has_14_unique_paths() -> None:
    assert len(audit.PATH_CATALOG) == 14
    assert [spec.index for spec in audit.PATH_CATALOG] == list(range(1, 15))
    keys = [spec.key for spec in audit.PATH_CATALOG]
    assert len(keys) == len(set(keys))


def test_config_parses_endpoints_and_fields() -> None:
    config = _config()
    assert config.platform == "demo"
    assert config.uid == "1"
    endpoint = config.endpoints[0]
    assert endpoint.path == "anonymous_json"
    assert endpoint.params["vmid"] == "{uid}"
    assert endpoint.fields["followers"] == "data.follower"
    assert endpoint.required_fields == ("data.follower", "data.following")


def test_config_cookies_env(monkeypatch) -> None:
    monkeypatch.setenv("ACK_TEST_COOKIES", "SESSDATA=abc; bili_jct=def")
    config = _config(cookies_env="ACK_TEST_COOKIES")
    assert config.cookies == {"SESSDATA": "abc", "bili_jct": "def"}


def test_config_to_dict_never_includes_cookies() -> None:
    config = _config(cookies={"SESSDATA": "secret"})
    data = config.to_dict()
    assert "cookies" not in data
    assert "secret" not in json.dumps(data, ensure_ascii=False)


def test_load_bilibili_audit_yaml_example() -> None:
    pytest.importorskip("yaml", reason="需要 [config] extra")
    path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "platforms"
        / "bilibili_audit.yaml"
    )
    config = audit.load_audit_config(path)
    assert config.platform == "bilibili"
    assert config.uid == "2"
    assert len(config.endpoints) == 2
    assert config.field_semantics["followers"]


# ── HTTP 探测器 ─────────────────────────────────────


def test_probe_http_success(monkeypatch) -> None:
    response = FakeResponse(
        data={"code": 0, "data": {"follower": 36, "following": 45}},
        text='{"code":0,"data":{"follower":36,"following":45}}',
    )
    monkeypatch.setattr(audit, "_http_client", lambda config: FakeClient(response))

    result = audit._probe_http_endpoints(
        _config(), _spec("anonymous_json"), browser_mode="never"
    )
    assert result.stop_reason == "completed"
    assert result.fields == {"followers": 36, "following": 45}
    assert result.login_required is False
    assert "relation" in result.evidence


def test_probe_http_risk_code_is_captcha(monkeypatch) -> None:
    response = FakeResponse(data={"code": -352}, text='{"code":-352}')
    monkeypatch.setattr(audit, "_http_client", lambda config: FakeClient(response))
    config = _config(
        endpoints=[
            {
                "path": "anonymous_json",
                "name": "relation",
                "url": "https://api.example.com/relation",
                "risk_codes": {"-352": "captcha"},
            }
        ]
    )

    result = audit._probe_http_endpoints(
        config, _spec("anonymous_json"), browser_mode="never"
    )
    assert result.stop_reason == "captcha"


def test_probe_http_missing_required_fields_is_empty(monkeypatch) -> None:
    response = FakeResponse(data={"code": 0, "data": {}}, text='{"code":0,"data":{}}')
    monkeypatch.setattr(audit, "_http_client", lambda config: FakeClient(response))

    result = audit._probe_http_endpoints(
        _config(), _spec("anonymous_json"), browser_mode="never"
    )
    assert result.stop_reason == "empty"
    assert result.fields == {}


def test_probe_http_without_endpoints_is_not_tested() -> None:
    result = audit._probe_http_endpoints(
        _config(endpoints=[]), _spec("anonymous_json"), browser_mode="never"
    )
    assert result.stop_reason == "not_tested"
    assert "endpoints" in result.note


def test_probe_login_http_without_cookies_is_not_tested() -> None:
    config = _config(
        endpoints=[
            {
                "path": "login_http",
                "name": "relation",
                "url": "https://api.example.com/relation",
            }
        ]
    )
    result = audit._probe_http_endpoints(
        config, _spec("login_http"), browser_mode="never"
    )
    assert result.stop_reason == "not_tested"
    assert "cookies" in result.note


# ── HTML / SSR 探测器 ───────────────────────────────


SSR_HTML = """
<html><head><title>Demo</title></head><body>
<div id="app"></div>
<script>window.__INITIAL_STATE__ = {"user": {"follower": 36, "following": 45}};</script>
<p>""" + ("这是用于通过空壳检测的正文。" * 30) + """</p>
</body></html>
"""


def test_probe_anonymous_html_ssr_completed(monkeypatch) -> None:
    response = FakeResponse(text=SSR_HTML)
    monkeypatch.setattr(audit, "_http_client", lambda config: FakeClient(response))

    result = audit._probe_anonymous_html(
        _config(), _spec("anonymous_html"), browser_mode="never"
    )
    assert result.stop_reason == "completed"
    assert result.status == 200
    assert "ssr_state" in result.evidence


def test_probe_anonymous_html_spa_shell_is_empty(monkeypatch) -> None:
    html = (
        "<html><body><div id='app'></div>"
        "<script src='/a.js'>" + ("var a=1;" * 100) + "</script>"
        "<script src='/b.js'>" + ("var b=1;" * 100) + "</script>"
        "<script src='/c.js'>" + ("var c=1;" * 100) + "</script>"
        "<div>loading</div>"
        "</body></html>"
    )
    response = FakeResponse(text=html)
    monkeypatch.setattr(audit, "_http_client", lambda config: FakeClient(response))

    result = audit._probe_anonymous_html(
        _config(), _spec("anonymous_html"), browser_mode="never"
    )
    assert result.stop_reason == "empty"


def test_probe_ssr_state_extracts_fields(monkeypatch) -> None:
    response = FakeResponse(text=SSR_HTML)
    monkeypatch.setattr(audit, "_http_client", lambda config: FakeClient(response))
    config = _config(
        state_fields={
            "followers": "user.follower",
            "following": "user.following",
        }
    )

    result = audit._probe_ssr_state(config, _spec("ssr_state"), browser_mode="never")
    assert result.stop_reason == "completed"
    assert result.fields == {"followers": 36, "following": 45}
    assert "initial_state" in result.evidence


def test_probe_ssr_state_without_marker_is_empty(monkeypatch) -> None:
    response = FakeResponse(text="<html><body>plain page</body></html>")
    monkeypatch.setattr(audit, "_http_client", lambda config: FakeClient(response))

    result = audit._probe_ssr_state(
        _config(), _spec("ssr_state"), browser_mode="never"
    )
    assert result.stop_reason == "empty"
    assert "no_state_marker" in result.evidence


# ── JS 包探测器 ─────────────────────────────────────


def test_probe_js_bundle_is_discovery(monkeypatch) -> None:
    fake = SimpleNamespace(
        scripts=["https://example.com/a.js", "https://example.com/b.js"],
        scanned_scripts=2,
        coverage=1.0,
        endpoints=[SimpleNamespace(resolved="https://api.example.com/list", value="/list")],
        base_urls=[SimpleNamespace(value="https://api.example.com")],
        field_hits=[SimpleNamespace(value="follower")],
        warnings=[],
    )
    monkeypatch.setattr(audit, "_js_hunt", lambda config: fake)

    result = audit._probe_js_bundle(
        _config(), _spec("js_bundle"), browser_mode="never"
    )
    assert result.stop_reason == "completed"
    assert result.coverage == {"discovered": 2, "scanned": 2, "ratio": 1.0}
    audit.score_path(result)
    assert result.rejected == "发现路径，不直接产出字段（只用于找接口）"


# ── 评分卡 ──────────────────────────────────────────


def _completed_result(**kwargs) -> audit.PathResult:
    data = {
        "platform": "demo",
        "test_url": "https://example.com",
        "path": "anonymous_json",
        "layer": "L1",
        "label": "匿名 JSON",
        "status": 200,
        "latency_ms": 120,
        "fields": {"followers": 36},
        "field_semantics": {"followers": "api.follower"},
        "stop_reason": "completed",
        "tested": True,
        "tested_at": "2026-11-08",
    }
    data.update(kwargs)
    return audit.PathResult(**data)


def test_score_path_computes_total() -> None:
    result = audit.score_path(_completed_result())
    assert result.rejected == ""
    assert result.total_score == 25
    assert result.scores == {
        "cost": 5,
        "dependency": 5,
        "stability": 5,
        "risk": 5,
        "maintenance": 5,
    }


def test_score_path_rejects_missing_semantics() -> None:
    result = audit.score_path(_completed_result(field_semantics={}))
    assert result.total_score == 0
    assert "口径" in result.rejected


def test_score_path_rejects_untested() -> None:
    result = audit.score_path(
        _completed_result(stop_reason="not_tested", tested=False)
    )
    assert "未测" in result.rejected


def test_score_path_rejects_failed_path() -> None:
    result = audit.score_path(_completed_result(stop_reason="captcha"))
    assert "未成功" in result.rejected


def test_score_overrides_are_clamped() -> None:
    result = audit.score_path(
        _completed_result(), overrides={"cost": 99, "dependency": 0}
    )
    assert result.scores["cost"] == 5
    assert result.scores["dependency"] == 1


# ── 选型建议 ────────────────────────────────────────


def test_recommend_picks_best_and_marks_not_production_ready() -> None:
    best = audit.score_path(_completed_result(path="anonymous_json", latency_ms=120))
    second = audit.score_path(
        _completed_result(
            path="login_http",
            layer="L1",
            login_required=True,
            latency_ms=300,
        )
    )
    recommendation = audit.recommend("demo", [best, second])

    assert recommendation.primary is not None
    assert recommendation.primary["path"] == "anonymous_json"
    assert recommendation.fallback is not None
    assert recommendation.fallback["path"] == "login_http"
    assert recommendation.rollback["env"] == "SOURCE_MODE"
    assert recommendation.rollback["values"] == ["anonymous_json", "login_http"]
    assert recommendation.production_ready is False
    assert any("30 目标" in item for item in recommendation.acceptance_gaps)


def test_recommend_fallback_is_not_allowed_when_vetoed() -> None:
    best = audit.score_path(_completed_result(path="anonymous_json"))
    vetoed = audit.score_path(
        _completed_result(path="mobile_api", layer="L1", field_semantics={})
    )
    recommendation = audit.recommend("demo", [best, vetoed])

    assert recommendation.primary is not None
    assert recommendation.fallback is not None
    assert recommendation.fallback["path"] == "mobile_api"
    assert recommendation.fallback["allowed_for_product"] is False
    assert "未通过硬门槛" in recommendation.fallback["reason"]


def test_recommend_without_eligible_path() -> None:
    failed = audit.score_path(_completed_result(stop_reason="blocked"))
    recommendation = audit.recommend("demo", [failed])
    assert recommendation.primary is None
    assert recommendation.production_ready is False
    assert any("没有通过硬门槛" in item for item in recommendation.acceptance_gaps)


# ── 完整跑批 / 输出 ─────────────────────────────────


def test_run_audit_writes_report_and_markdown(monkeypatch, tmp_path: Path) -> None:
    response = FakeResponse(
        data={"code": 0, "data": {"follower": 36, "following": 45}},
        text='{"code":0,"data":{"follower":36,"following":45}}',
    )
    monkeypatch.setattr(audit, "_http_client", lambda config: FakeClient(response))

    run = audit.run_audit(
        _config(),
        only=["anonymous_json"],
        browser_mode="never",
        out_dir=tmp_path,
    )
    report_path, markdown_path = run.write(tmp_path)

    assert len(run.results) == 14
    assert run.hard_stop == ""
    assert run.exit_code() == 0
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["total"] == 1
    assert data["success_rate"] == 1.0
    assert data["meta"]["recommendation"]["primary"]["path"] == "anonymous_json"

    markdown = markdown_path.read_text(encoding="utf-8")
    assert "| 1 |" in markdown
    assert "| 14 |" in markdown
    assert "未测路径怎么测" in markdown


def test_run_audit_hard_stops_on_captcha(monkeypatch, tmp_path: Path) -> None:
    response = FakeResponse(data={"code": -352}, text='{"code":-352}')
    monkeypatch.setattr(audit, "_http_client", lambda config: FakeClient(response))
    monkeypatch.setattr(audit, "_notify_hard_stop", lambda config, result: None)
    config = _config(
        endpoints=[
            {
                "path": "anonymous_json",
                "name": "relation",
                "url": "https://api.example.com/relation",
                "risk_codes": {"-352": "captcha"},
            }
        ]
    )

    run = audit.run_audit(
        config,
        only=["anonymous_json", "browser_xhr"],
        browser_mode="never",
        out_dir=tmp_path,
    )

    assert run.hard_stop == "captcha"
    assert run.exit_code() == 2
    browser = next(item for item in run.results if item.path == "browser_xhr")
    assert browser.stop_reason == "not_tested"
    assert "硬停" in browser.note


def test_run_audit_rejects_unknown_only(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="未知路径"):
        audit.run_audit(_config(), only=["nope"], out_dir=tmp_path)
