"""无浏览器测试：JS 包找接口。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit import js_bundle
from anti_crawl_kit.js_bundle import (
    analyze_js,
    extract_api_hosts,
    extract_js_constants,
    extract_nested_scripts,
    extract_script_urls,
    hunt_api,
    parse_cookie_header,
)

HTML = """<!doctype html>
<html><head>
  <script src="/static/js/main_6a120aa.js"></script>
  <script src="https://cdn.example.com/app.js"></script>
  <script src="/static/js/main_6a120aa.js"></script>
  <script>var inline = 1;</script>
</head><body></body></html>
"""

# 模拟百家号压缩后的业务代码
JS = """
!function(){
  var a={default:{baseURL:"/my",withCredentials:!0}};
  var fields=["dynamicNum","subscribeNum","fansNum"];
  c.prototype.getUserInfo=function(){
    a["default"].get("/dynamic_num").then(function(e){
      0===e.data.status&&t.data.set("bjhInfo",e.data.data)
    })
  };
}();
"""


# ── script 提取 ──────────────────────────────────────
def test_extract_script_urls_resolves_and_dedupes():
    urls = extract_script_urls(HTML, "https://www.baidu.com/my/index")
    assert urls == [
        "https://www.baidu.com/static/js/main_6a120aa.js",
        "https://cdn.example.com/app.js",
    ]


def test_extract_nested_scripts_from_loader():
    loader = 'import("./chunk-main.js");var x="static/js/vendor.js";'
    urls = extract_nested_scripts(loader, "https://x.com/static/js/main.js")
    assert "https://x.com/static/js/chunk-main.js" in urls
    assert "https://x.com/static/js/vendor.js" in urls


def test_extract_script_urls_includes_modulepreload():
    html = (
        '<script type="module" src="/assets/index-abc.js"></script>'
        '<link rel="modulepreload" href="/assets/profile-def.js">'
        '<link rel="preload" as="script" href="/assets/vendor-ghi.js">'
        '<link rel="stylesheet" href="/assets/app.css">'
    )
    urls = extract_script_urls(html, "https://example.com/u/1")
    assert urls == [
        "https://example.com/assets/index-abc.js",
        "https://example.com/assets/profile-def.js",
        "https://example.com/assets/vendor-ghi.js",
    ]


def test_extract_nested_scripts_accepts_non_keyword_chunk_names():
    # 快手实测：动态 chunk 叫 profile-xxx.js / workbench-xxx.js，
    # 旧实现只认 chunk/main/index/vendor，会漏掉整棵 import 树。
    js = 'import("./profile-Cgkt1CT6.js");import("./workbench-Dojrq00F.js");'
    urls = extract_nested_scripts(js, "https://x.com/assets/index-CJkW9KAF.js")
    assert "https://x.com/assets/profile-Cgkt1CT6.js" in urls
    assert "https://x.com/assets/workbench-Dojrq00F.js" in urls


# ── baseURL + 接口 ───────────────────────────────────
def test_analyze_js_relative_base_url():
    bases, endpoints, _ = analyze_js(
        JS, source="main.js", page_url="https://www.baidu.com/my/index"
    )
    assert [b.value for b in bases] == ["/my"]
    assert any(e.resolved == "https://www.baidu.com/my/dynamic_num" for e in endpoints)
    assert any(e.method == "GET" and e.value == "/dynamic_num" for e in endpoints)


def test_analyze_js_absolute_base_url():
    js = 'axios.create({baseURL:"https://api.example.com/v1"});api.get("/users")'
    _, endpoints, _ = analyze_js(
        js, source="app.js", page_url="https://www.example.com/"
    )
    assert any(e.resolved == "https://api.example.com/v1/users" for e in endpoints)


def test_analyze_js_fetch_and_post():
    js = 'fetch("/api/list");client.post("/api/save")'
    _, endpoints, _ = analyze_js(
        js, source="app.js", page_url="https://example.com/page"
    )
    resolved = {e.resolved for e in endpoints}
    assert "https://example.com/api/list" in resolved
    assert "https://example.com/api/save" in resolved


def test_analyze_js_skips_static_assets():
    js = 'img.get("/static/logo.png");api.get("/api/real")'
    _, endpoints, _ = analyze_js(
        js, source="app.js", page_url="https://example.com/"
    )
    resolved = {e.resolved for e in endpoints}
    assert "https://example.com/api/real" in resolved
    assert all(".png" not in url for url in resolved)


def test_analyze_js_field_context():
    _, _, hits = analyze_js(
        JS,
        source="main.js",
        page_url="https://www.baidu.com/my/index",
        fields=["dynamicNum", "bjhInfo"],
    )
    names = {h.value for h in hits}
    assert "dynamicNum" in names
    assert "bjhInfo" in names
    assert all(h.context for h in hits)


def test_extract_api_hosts():
    js = 'var a="//api.bilibili.com/x";var b="https://open.example.org/path";'
    hosts = extract_api_hosts(js)
    assert "api.bilibili.com" in hosts
    assert "open.example.org" in hosts


def test_extract_api_hosts_skips_placeholders():
    js = 'var a="//api.domain.com/x";var b="https://your-api.com/y";'
    assert extract_api_hosts(js) == []


def test_analyze_js_api_host_override():
    js = 'client.request({url:"/x/web-interface/popular",method:"GET"})'
    _, endpoints, _ = analyze_js(
        js,
        source="app.js",
        page_url="https://www.bilibili.com/v/popular/all",
        api_host="api.bilibili.com",
    )
    assert any(
        e.resolved == "https://api.bilibili.com/x/web-interface/popular"
        for e in endpoints
    )


def test_analyze_js_dsl_endpoint_definition():
    """声明式 DSL：helper(`接口名`, `/api/xxx`, {配置})，路径在第 2 个参数。

    真实案例（opencode 控制台）：
        .add(Ue(`status`, `/api/go/status`, {success:..., error:...}))
        .add(UN(`drawerUsage`, `/drawer/usage`, {success:...}))
    旧实现只认 .get("/x") / fetch("/x")，这类会被整条漏掉。
    """
    js = """
    Tr = we(`go`).add(
      Ue(`status`, `/api/go/status`, {query: I({fresh: F(jt(`true`))}), success: Cr, error: P})
    ).add(D(`createCheckout`, `/api/go/checkout`, {payload: Kt, success: Lt, error:[O,E]}));
    """
    _, endpoints, _ = analyze_js(
        js, source="go.js", page_url="https://opencode.ai/console/org_x/go"
    )
    values = {e.value for e in endpoints}
    assert "/api/go/status" in values
    assert "/api/go/checkout" in values


def test_analyze_js_dsl_skips_single_segment_and_static():
    """DSL 提取要过滤单段噪音与静态资源。"""
    js = """
    x.add(a(`name`, `/foo`, {k:1}));
    y.add(b(`img`, `/static/logo.png`, {}));
    """
    _, endpoints, _ = analyze_js(
        js, source="a.js", page_url="https://example.com/page"
    )
    values = {e.value for e in endpoints}
    assert "/foo" not in values
    assert all(".png" not in v for v in values)


# ── JS 常量 + 拼接 ───────────────────────────────────
def test_extract_js_constants_filters_non_urls():
    js = (
        'var BASE="https://www.biquge.mom";'
        'var APIBI="https://apibi.cc";'
        'var n=1; var s="hello";'
    )
    constants = extract_js_constants(js)
    assert constants["BASE"] == "https://www.biquge.mom"
    assert constants["APIBI"] == "https://apibi.cc"
    assert "n" not in constants
    assert "s" not in constants


def test_extract_js_constants_fallback_value():
    js = """
    var BASE = String(SITE.DATA_BASE || '/data').replace(/\\/+$/, '');
    var APIBI = String(SITE.APIBI_HOST || 'https://apibi.cc').replace(/\\/+$/, '');
    """
    constants = extract_js_constants(js)
    assert constants["BASE"] == "/data"
    assert constants["APIBI"] == "https://apibi.cc"


def test_analyze_js_constant_concat():
    js = """
    var BASE = "https://www.biquge.mom";
    var APIBI = "https://apibi.cc";
    getJson(BASE + "/data/author-docs-" + bucket + ".json");
    fetchTimeout(APIBI + "/api/" + path + "?token=" + tk);
    """
    _, endpoints, _ = analyze_js(
        js, source="data.js", page_url="https://www.biquge.mom/"
    )
    resolved = {e.resolved for e in endpoints}
    assert "https://apibi.cc/api/" in resolved
    assert "https://www.biquge.mom/data/author-docs-" in resolved


def test_analyze_js_constant_not_treated_as_base_url():
    js = 'var API = "https://api.example.com"; getJson(API + "/v1/list");'
    bases, _, _ = analyze_js(
        js, source="app.js", page_url="https://www.example.com/page"
    )
    assert all(item.kind != "base_url" for item in bases)
    assert any(item.kind == "constant" for item in bases)


# ── 请求走 AntiRiskHttpClient ────────────────────────
def test_http_get_uses_anti_risk_client(monkeypatch):
    pytest.importorskip("curl_cffi", reason="需要 [http] extra")
    from anti_crawl_kit import http as http_mod
    from anti_crawl_kit import js_bundle as js_mod

    calls = {}

    class FakeResult:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, platform, **kwargs):
            calls["platform"] = platform

        def get_text(self, url, **kwargs):
            calls["url"] = url
            return FakeResult()

    monkeypatch.setattr(http_mod, "AntiRiskHttpClient", FakeClient)

    status, text, error = js_mod._http_get("https://example.com/x")
    assert (status, text, error) == (200, "ok", "")
    assert calls["platform"] == "js_bundle"


# ── 完整流程 ─────────────────────────────────────────
def test_hunt_api_with_mock(monkeypatch):
    def fake_get(url, **kwargs):
        if url.endswith("/my/index"):
            return 200, HTML, ""
        if "main_6a120aa.js" in url:
            return 200, JS, ""
        return 404, "", "HTTP 404"

    monkeypatch.setattr(js_bundle, "_http_get", fake_get)

    result = hunt_api(
        "https://www.baidu.com/my/index",
        fields=["dynamicNum"],
        max_scripts=5,
    )

    assert len(result.scripts) == 2
    assert [b.value for b in result.base_urls] == ["/my"]
    assert any(
        e.resolved == "https://www.baidu.com/my/dynamic_num" for e in result.endpoints
    )
    assert any(h.value == "dynamicNum" for h in result.field_hits)
    # 第二个 script 404，应记录失败但不影响主流程
    assert any("cdn.example.com" in f["url"] for f in result.failed_scripts)


def test_hunt_api_follows_chunks_from_large_js(monkeypatch):
    """旧实现只跟进 <3KB 的加载器；大 chunk 里的 import 会整棵漏掉。"""
    html = '<script src="/assets/index.js"></script>'
    index_js = "x" * 4000 + ';import("./profile-abc.js");'
    profile_js = 'client.get("/api/profile")'

    def fake_get(url, **kwargs):
        if url.endswith("/page"):
            return 200, html, ""
        if url.endswith("/assets/index.js"):
            return 200, index_js, ""
        if url.endswith("/assets/profile-abc.js"):
            return 200, profile_js, ""
        return 404, "", "HTTP 404"

    monkeypatch.setattr(js_bundle, "_http_get", fake_get)
    result = hunt_api("https://example.com/page", max_scripts=10)

    assert "https://example.com/assets/profile-abc.js" in result.scripts
    assert result.scanned_scripts == 2
    assert result.coverage == 1.0
    assert any(
        e.resolved == "https://example.com/api/profile" for e in result.endpoints
    )


def test_hunt_api_extra_scripts_and_coverage(monkeypatch):
    html = '<script src="/assets/main.js"></script>'

    def fake_get(url, **kwargs):
        if url.endswith("/assets/main.js"):
            return 200, "var a=1;", ""
        if url.endswith("/dyn.js"):
            return 200, 'fetch("/api/list")', ""
        return 404, "", "HTTP 404"

    monkeypatch.setattr(js_bundle, "_http_get", fake_get)
    result = hunt_api(
        "https://example.com/page",
        html=html,
        extra_scripts=["https://cdn.example.com/dyn.js"],
        max_scripts=10,
    )

    assert len(result.scripts) == 2
    assert result.scanned_scripts == 2
    assert result.coverage == 1.0
    assert result.source_mode == "html+browser"
    assert any(
        e.resolved == "https://example.com/api/list" for e in result.endpoints
    )


def test_hunt_api_warns_on_spa_shell(monkeypatch):
    html = '<script src="/assets/index.js"></script>'

    def fake_get(url, **kwargs):
        if url.endswith("/assets/index.js"):
            return 200, "var a=1;", ""
        return 404, "", "HTTP 404"

    monkeypatch.setattr(js_bundle, "_http_get", fake_get)
    result = hunt_api("https://example.com/page", html=html, max_scripts=10)

    assert any("SPA" in item for item in result.warnings)


def test_hunt_api_progress_callback(monkeypatch):
    """长任务进度回调：下载过程中报 done/total，结束后补一次 100%。"""
    html = '<script src="/a.js"></script><script src="/b.js"></script>'
    calls = []

    def fake_get(url, **kwargs):
        if url.endswith(".js"):
            return 200, "var a=1;", ""
        return 404, "", "HTTP 404"

    monkeypatch.setattr(js_bundle, "_http_get", fake_get)
    result = hunt_api(
        "https://example.com/page",
        html=html,
        max_scripts=5,
        progress=lambda done, total, src: calls.append((done, total, src)),
    )

    assert result.scanned_scripts == 2
    assert calls[0][0] == 1
    assert calls[-1] == (2, 2, "")


def test_hunt_api_no_fetch_lists_scripts_only():
    result = hunt_api(
        "https://www.baidu.com/my/index",
        html=HTML,
        fetch=False,
    )
    assert len(result.scripts) == 2
    assert result.endpoints == []


# ── cookie 解析 ──────────────────────────────────────
def test_parse_cookie_header():
    cookies = parse_cookie_header("BDUSS=abc; STOKEN=def=ghi;  ; PTOKEN=xyz")
    assert cookies == {"BDUSS": "abc", "STOKEN": "def=ghi", "PTOKEN": "xyz"}
