"""智能 API 发现引擎 —— 新平台接入第一步。

提供 6 种发现策略：
  1. Performance API 资源捕获（页面实际调用了哪些接口）
  2. .json 后缀探测（Reddit 风格）
  3. __INITIAL_STATE__ 提取（SSR 页面）
  4. Pinia/Vuex Store 读取（Vue SPA）
  5. 搜索端点探测
  6. 框架检测

用法::

    with BrowserSession(data_dir="./profiles/demo") as page:
        manifest = ApiDiscoverer(page).discover("https://example.com")
        print(manifest["framework"])
        for ep in manifest["api_endpoints"]:
            print(ep["url"])

⚠ 注意：deep_probe=True（默认）会对发现的接口发起 GET 请求验证是否返回 JSON。
   只对公开站点使用；对已登录的敏感站点建议 deep_probe=False。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .stealth import (
    FRAMEWORK_DETECT_JS,
    INITIAL_STATE_JS,
    NETWORK_CAPTURE_JS,
    PINIA_STORE_JS,
)

logger = logging.getLogger(__name__)

SMART_API_DISCOVERY_JS = """(async () => {
    var entries = performance.getEntriesByType('resource');
    var apiUrls = entries
        .map(function(e) { return e.name; })
        .filter(function(url) {
            var lower = url.toLowerCase();
            return (lower.indexOf('/api/') !== -1 || lower.indexOf('/v1/') !== -1
                || lower.indexOf('/v2/') !== -1 || lower.indexOf('/v3/') !== -1
                || lower.indexOf('/x/') !== -1 || lower.indexOf('.json') !== -1
                || lower.indexOf('graphql') !== -1 || lower.indexOf('search') !== -1
                || lower.indexOf('feed') !== -1 || lower.indexOf('hot') !== -1
                || lower.indexOf('trending') !== -1 || lower.indexOf('list') !== -1)
                && lower.indexOf('.js') === -1 && lower.indexOf('.css') === -1
                && lower.indexOf('.png') === -1 && lower.indexOf('.jpg') === -1
                && lower.indexOf('.svg') === -1 && lower.indexOf('.woff') === -1;
        });

    var seen = {};
    var unique = [];
    apiUrls.forEach(function(url) {
        try {
            var u = new URL(url);
            var key = u.pathname;
            if (!seen[key]) {
                seen[key] = true;
                unique.push(url);
            }
        } catch(e) {}
    });

    var results = [];
    for (var i = 0; i < Math.min(unique.length, 20); i++) {
        try {
            var url = unique[i];
            var resp = await fetch(url, { credentials: 'include' });
            if (!resp.ok) continue;
            var ct = resp.headers.get('content-type') || '';
            if (ct.indexOf('json') === -1 && ct.indexOf('javascript') === -1) continue;
            var json = await resp.json();
            results.push({ url: url, status: resp.status, body: json });
        } catch(e) {}
    }
    return results;
})()"""

PROBE_JSON_SUFFIX_JS = """(async () => {
    var currentUrl = window.location.href;
    if (currentUrl.indexOf('/api/') !== -1 || currentUrl.indexOf('/x/') !== -1) return null;
    var jsonUrl = currentUrl.indexOf('?') !== -1
        ? currentUrl.replace('?', '.json?')
        : currentUrl.replace(/\\/$/, '') + '.json';
    try {
        var r = await fetch(jsonUrl, { credentials: 'include' });
        if (!r.ok) return null;
        var ct = r.headers.get('content-type') || '';
        if (ct.indexOf('json') === -1) return null;
        return await r.json();
    } catch(e) { return null; }
})()"""

PROBE_SEARCH_JS = """(async () => {
    var origin = window.location.origin;
    var candidates = [
        origin + '/search?q=test',
        origin + '/api/search?q=test',
        origin + '/api/search?keyword=test',
        origin + '/api/v1/search?q=test',
    ];
    var results = [];
    for (var i = 0; i < candidates.length; i++) {
        try {
            var resp = await fetch(candidates[i]);
            if (resp.ok) {
                var ct = resp.headers.get('content-type') || '';
                results.push({ url: candidates[i], ok: true, isJson: ct.indexOf('json') !== -1 });
            }
        } catch(e) {}
    }
    return results;
})()"""


class ApiDiscoverer:
    """智能 API 发现器。"""

    def __init__(self, page: Any, timeout: int = 15, deep_probe: bool = True):
        self._page = page
        self._timeout = timeout
        self._deep_probe = deep_probe

    def discover(self, url: str) -> dict[str, Any]:
        """执行全量发现，返回可落盘的 manifest。"""
        self._page.goto(url, timeout=self._timeout * 1000)
        time.sleep(3)

        manifest: dict[str, Any] = {
            "url": url,
            "framework": self.detect_framework(),
            "api_endpoints": [],
            "initial_state": None,
            "stores": [],
            "network_urls": [],
        }

        fw = manifest["framework"]
        manifest["initial_state"] = self._extract_initial_state()
        if fw.get("pinia") or fw.get("vuex"):
            manifest["stores"] = self._read_stores()

        manifest["network_urls"] = self._network_urls()
        if self._deep_probe:
            manifest["api_endpoints"] = self._discover_api_endpoints()
            json_data = self._probe_json_suffix()
            if json_data:
                manifest["api_endpoints"].append({
                    "url": url.rstrip("/") + ".json",
                    "method": "GET",
                    "source": ".json_suffix",
                    "has_data": True,
                })
            manifest["api_endpoints"].extend(self._probe_search())

        logger.info(
            "[api-discovery] %s: %d endpoints, framework=%s",
            url,
            len(manifest["api_endpoints"]),
            [k for k, v in fw.items() if v],
        )
        return manifest

    def detect_framework(self) -> dict[str, bool]:
        try:
            return self._page.evaluate(FRAMEWORK_DETECT_JS, isolated_context=False)
        except Exception:
            return {}

    def _extract_initial_state(self) -> Any:
        try:
            return self._page.evaluate(INITIAL_STATE_JS, isolated_context=False)
        except Exception:
            return None

    def _read_stores(self) -> list[dict]:
        try:
            return self._page.evaluate(PINIA_STORE_JS, isolated_context=False)
        except Exception:
            return []

    def _network_urls(self) -> list[dict]:
        try:
            return self._page.evaluate(NETWORK_CAPTURE_JS)
        except Exception:
            return []

    def _discover_api_endpoints(self) -> list[dict]:
        try:
            raw = self._page.evaluate(SMART_API_DISCOVERY_JS)
            return [
                {"url": item.get("url"), "status": item.get("status"), "body": item.get("body")}
                for item in (raw or [])
                if isinstance(item, dict)
            ]
        except Exception:
            return []

    def _probe_json_suffix(self) -> Any:
        try:
            return self._page.evaluate(PROBE_JSON_SUFFIX_JS)
        except Exception:
            return None

    def _probe_search(self) -> list[dict]:
        try:
            return self._page.evaluate(PROBE_SEARCH_JS)
        except Exception:
            return []
