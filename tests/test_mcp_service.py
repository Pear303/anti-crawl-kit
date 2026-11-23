"""MCP ToolService 测试（离线，不依赖 mcp 包）。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from anti_crawl_kit.mcp_server import JobManager, ToolService


class Resp:
    def __init__(self, text: str, status_code: int = 200, url: str = "https://example.com/") -> None:
        self.text = text
        self.status_code = status_code
        self.url = url


class FakeHTTP:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def get_text(self, url: str, **kwargs) -> Resp:
        return Resp(self.pages.get(url, ""), url=url)

    def get_json(self, url: str, **kwargs) -> Resp:
        response = Resp(self.pages.get(url, ""), url=url)
        response.data = json.loads(response.text) if response.text else None
        return response


SSR_HTML = (
    "<html><head><title>商品列表</title></head><body>"
    + "<p>" + "正文内容" * 1200 + "</p>"
    + '<div class="item">x</div>' * 3
    + "</body></html>"
)
SPA_HTML = "<html><body><div id='app'></div><script src='/app.js'></script></body></html>"
NEXT_HTML = (
    '<html><body><script id="__NEXT_DATA__" type="application/json">'
    '{"props":{"pageProps":{"items":[1,2]}}}</script></body></html>'
)


def _service(pages: dict[str, str], **kwargs) -> ToolService:
    return ToolService(http_client=FakeHTTP(pages), **kwargs)


def test_probe_url_spa_decision() -> None:
    service = _service({"https://example.com/": SPA_HTML})
    result = service.probe_url("https://example.com/")
    assert result["status"] == 200
    assert result["decision"] == "browser_or_js"
    assert result["next_actions"]


def test_probe_url_ssr_decision() -> None:
    service = _service({"https://example.com/": SSR_HTML})
    result = service.probe_url("https://example.com/", selector="div.item")
    assert result["decision"] == "parse_html"
    assert result["empty_shell"]["matched"] == 3


def test_allow_hosts_blocks() -> None:
    service = _service({"https://evil.com/": SSR_HTML}, allow_hosts=("example.com",))
    with pytest.raises(PermissionError, match="allow_hosts"):
        service.probe_url("https://evil.com/")


def test_classify_page() -> None:
    service = _service({"https://example.com/": SPA_HTML})
    result = service.classify_page("https://example.com/")
    assert result["decision"] == "browser_or_js"
    assert "shape" in result


def test_probe_url_captcha_wall_stops() -> None:
    # 验证墙的 kind 是 captcha_wall；决策必须是 stop，不能让 Agent 继续开浏览器
    html = (
        "<html><head><title>访问验证</title></head><body>"
        "<p>请完成安全验证后继续访问</p>"
        + "<p>" + "正文" * 200 + "</p>"
        + "</body></html>"
    )
    service = _service({"https://example.com/": html})
    result = service.probe_url("https://example.com/")
    assert result["shape"]["kind"] == "captcha_wall"
    assert result["decision"] == "stop"
    assert "停" in result["next_actions"][0]


def test_extract_state() -> None:
    service = _service({"https://example.com/": NEXT_HTML})
    result = service.extract_state("https://example.com/")
    assert result["ok"] is True
    assert result["key"] == "next_data"
    assert "items" in result["preview"]


def test_extract_state_redacts_secret() -> None:
    html = (
        "<script>window.__INITIAL_STATE__ = "
        '{"token":"abcdef123456","name":"demo"}</script>'
    )
    service = _service({"https://example.com/": html})
    result = service.extract_state("https://example.com/")
    assert "abcdef123456" not in result["preview"]
    assert "<redacted>" in result["preview"]


def test_list_capabilities() -> None:
    service = _service({})
    data = service.list_capabilities()
    assert "probe_url" in data["tools"]
    assert "assess_project" in data["tools"]
    # 补 G2 断裂点：MCP 必须能直接拿到选择器并生成评估配置
    assert "dom_probe" in data["tools"]
    assert "draft_assessment" in data["tools"]
    assert "http_get" in data["steps"]
    assert "curl_cffi" in data["optional_deps"]
    assert data["browser_channel"] == "auto"


def test_dom_probe_service(monkeypatch) -> None:
    from anti_crawl_kit.tools import dom_probe as dom_module

    seen: dict[str, object] = {}

    def fake_probe(url: str, **kwargs):
        seen.update(kwargs)
        return {
            "ok": True,
            "tool": "dom_probe",
            "url": url,
            "item_candidates": [{"selector": "article.item", "count": 3, "outline": []}],
        }

    monkeypatch.setattr(dom_module, "probe_dom", fake_probe)
    service = _service({}, allow_hosts=("example.com",))
    result = service.dom_probe("https://example.com/list", headless=False, wait=1.0)

    assert result["ok"] is True
    assert result["item_candidates"][0]["selector"] == "article.item"
    assert seen["headless"] is False
    assert seen["wait"] == 1.0


def test_dom_probe_service_blocked_by_allow_hosts(monkeypatch) -> None:
    from anti_crawl_kit.tools import dom_probe as dom_module

    monkeypatch.setattr(dom_module, "probe_dom", lambda url, **kwargs: {"ok": True})
    service = _service({}, allow_hosts=("example.com",))
    with pytest.raises(PermissionError, match="allow_hosts"):
        service.dom_probe("https://evil.com/")


def test_draft_assessment_service(tmp_path: Path) -> None:
    service = _service({})
    probe = {
        "url": "https://example.com/list",
        "item_candidates": [
            {
                "selector": "article.item",
                "count": 4,
                "sample": "标题 1",
                "outline": [
                    {"selector": "h2", "text": "标题 1", "href": ""},
                    {"selector": "span.price", "text": "¥19.9", "href": ""},
                ],
            }
        ],
    }
    result = service.draft_assessment(probe, out_dir=str(tmp_path))

    assert result["item_selector"] == "article.item"
    assert Path(result["config_path"]).exists()
    assert Path(result["audit_path"]).exists()
    assert "article.item" in Path(result["config_path"]).read_text(encoding="utf-8")


def test_list_capabilities_channel() -> None:
    service = ToolService(http_client=FakeHTTP({}), browser_channel="msedge")
    assert service.list_capabilities()["browser_channel"] == "msedge"


def test_job_manager_success() -> None:
    manager = JobManager(max_workers=1)
    job_id = manager.submit("demo", lambda: {"ok": True})
    for _ in range(100):
        if manager.get(job_id)["status"] == "done":
            break
        time.sleep(0.01)
    data = manager.get(job_id, include_result=True)
    assert data["status"] == "done"
    assert data["result"] == {"ok": True}


def test_job_manager_failure() -> None:
    manager = JobManager(max_workers=1)

    def boom():
        raise RuntimeError("boom")

    job_id = manager.submit("demo", boom)
    for _ in range(100):
        if manager.get(job_id)["status"] == "failed":
            break
        time.sleep(0.01)
    data = manager.get(job_id)
    assert data["status"] == "failed"
    assert "boom" in data["error"]
    with pytest.raises(KeyError):
        manager.get("missing")


def test_smoke_rejects_unknown_only() -> None:
    service = _service({})
    with pytest.raises(ValueError, match="没有匹配到用例"):
        service.smoke_test(only="no-such-case")


def _write_recipe(path: Path, steps: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "id": "demo",
                "allow_hosts": ["example.com"],
                "inputs": {"url": {"type": "string"}},
                "steps": steps,
            }
        ),
        encoding="utf-8",
    )


def test_run_recipe_via_service(tmp_path: Path) -> None:
    recipe_path = tmp_path / "r.json"
    _write_recipe(
        recipe_path,
        [
            {"id": "fetch", "uses": "http_get", "with": {"url": "${inputs.url}"}},
            {
                "id": "count",
                "uses": "count_html",
                "with": {"html": "${steps.fetch.text}", "selector": "div.item"},
            },
        ],
    )
    service = _service({"https://example.com/": SSR_HTML})
    result = service.run_recipe(
        str(recipe_path), inputs={"url": "https://example.com/"}
    )
    assert result["status"] == "done"
    assert result["context"]["steps"]["count"]["count"] == 3
    assert result["exit_code"] == 0


def test_resume_recipe_via_service(tmp_path: Path) -> None:
    recipe_path = tmp_path / "r.json"
    _write_recipe(
        recipe_path,
        [
            {"id": "fetch", "uses": "http_get", "with": {"url": "${inputs.url}"}},
            {
                "id": "pick",
                "kind": "agent",
                "ask": {"prompt": "选选择器", "context_keys": ["steps.fetch.status"]},
            },
            {
                "id": "count",
                "uses": "count_html",
                "with": {
                    "html": "${steps.fetch.text}",
                    "selector": "${steps.pick.selector}",
                },
            },
        ],
    )
    service = _service({"https://example.com/": SSR_HTML})
    first = service.run_recipe(str(recipe_path), inputs={"url": "https://example.com/"})
    assert first["status"] == "needs_agent"
    assert first["handoff"]["step_id"] == "pick"

    resumed = service.resume_recipe(first, {"selector": "div.item"})
    assert resumed["status"] == "done"
    assert resumed["context"]["steps"]["count"]["count"] == 3


def test_prune_profile_cache_via_service(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    (profile / "Default/Cache").mkdir(parents=True)
    (profile / "Default/Cache/data").write_bytes(b"x" * 300_000)
    (profile / "Default/Network").mkdir(parents=True)
    (profile / "Default/Network/Cookies").write_bytes(b"cookie")
    service = _service({})
    result = service.prune_profile_cache(
        str(profile), max_cache_mb=0.01, max_total_mb=0.01
    )
    assert result["reason"] == "pruned"
    assert (profile / "Default/Network/Cookies").exists()


def test_assess_project_job(tmp_path: Path, monkeypatch) -> None:
    # 注意：anti_crawl_kit.assess 既是子模块又被公开成同名函数（惰性导出），
    # from anti_crawl_kit import assess 拿到的是函数，必须用 import_module 取模块。
    import importlib

    assess_module = importlib.import_module("anti_crawl_kit.assess")

    monkeypatch.setattr(
        assess_module, "_default_fetch", lambda url: (200, SSR_HTML, "")
    )
    config = {
        "platform": "demo",
        "url": "https://example.com/",
        "item_selector": "div.item",
        "field_selectors": {"title": {"selector": ".name"}},
        "contract": {
            "name": "demo",
            "id_field": "title",
            "min_items": 1,
            "fields": {"title": {"type": "string"}},
        },
        "runs": 2,
        "interval_ms": 0,
        "audit_config": "",
    }
    html = "".join(
        f'<div class="item"><span class="name">n{i}</span></div>' for i in range(3)
    )
    monkeypatch.setattr(
        assess_module, "_default_fetch", lambda url: (200, html, "")
    )
    path = tmp_path / "assess.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    service = _service({})
    job_id = service.assess_project(str(path), out_dir=str(tmp_path / "out"))
    for _ in range(200):
        job = service.get_job(job_id)
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.01)
    result = service.get_job_result(job_id)
    assert job["status"] == "done", result.get("error")
    assert result["result"]["decision"] in ("go", "conditional_go", "no_go")
    assert (tmp_path / "out" / "assessment.json").exists()
