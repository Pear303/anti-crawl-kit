"""从页面 JS 包里找真实接口 —— 不猜、不开浏览器。

背景（2026-11 百家号实战）：
    ``www.baidu.com/my/index`` 的 HTML 里，动态/关注/粉丝三个数字是空的，
    服务端只下发一个 ``main_xxx.js``。这个 JS 里写着::

        a["default"].get("/dynamic_num")

    而请求客户端的 ``baseURL = "/my"``。所以真实接口是::

        GET https://www.baidu.com/my/dynamic_num

    带上登录 cookie 直接请求，返回 ``dynamicNum / subscribeNum / fansNum``。
    全程零浏览器。

适用：
    - HTML 里数据是空壳，数字/列表由前端 JS 拉取
    - 接口没有签名，只需要登录 cookie
    - 想避免上浏览器（快 10~100 倍）

不适用：
    - 接口需要 JS 运行时生成签名（a_bogus / w_rid / x-s）
    - 参数由 WASM / 混淆代码计算
    - 强风控，HTTP 直连 403 / 429

用法::

    from anti_crawl_kit.js_bundle import hunt_api

    result = hunt_api(
        page_url="https://www.baidu.com/my/index",
        html=open("page.html", encoding="utf-8").read(),
        cookies={"BDUSS": "..."},
        fields=["fansNum", "subscribeNum"],
    )
    for ep in result.endpoints:
        print(ep.resolved, ep.context)

命令行::

    python tools/js_api_hunt.py --url "https://www.baidu.com/my/index" \\
        --cookie "BDUSS=..." --field fansNum
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

from .user_agent import get_ua_for_impersonate

logger = logging.getLogger(__name__)

# 与 _http_get 里的 impersonate 保持同版本，避免 UA 与 TLS 指纹矛盾
DEFAULT_UA = get_ua_for_impersonate("chrome120")

# ── HTML / JS 提取正则 ──────────────────────────────────
_SCRIPT_SRC_RE = re.compile(
    r"""<script[^>]+src\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)

# Vite / 现代构建工具常用 <link rel="modulepreload" href="..."> 预加载入口 chunk。
# 只读 <script src> 会漏掉这类入口，后面自然也就找不到动态 chunk。
_LINK_TAG_RE = re.compile(r"""<link\b[^>]*>""", re.IGNORECASE)
_LINK_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_LINK_REL_RE = re.compile(r"""rel\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_LINK_AS_RE = re.compile(r"""\bas\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

# baseURL / baseUrl / base_url / apiBase ...
_BASE_URL_RE = re.compile(
    r"""(?:baseURL|baseUrl|base_url|BASE_URL|apiBase|api_base|API_BASE|API_ROOT)"""
    r"""\s*[:=]\s*["']([^"']{1,300})["']"""
)

# .get("/x") / .post('/x') / .put(`/x`) / .delete("/x")
_HTTP_VERB_RE = re.compile(
    r"""\.(get|post|put|delete|patch)\s*\(\s*[`"'](/[^`"']{0,300})[`"']""",
    re.IGNORECASE,
)

# fetch("/x") / axios("/x")
_FETCH_RE = re.compile(
    r"""(?:fetch|axios)\s*\(\s*[`"'](/[^`"']{0,300})[`"']""", re.IGNORECASE
)

# 对象字面量写法：{url:"/x/web-interface/popular", method:"GET"}
# B站、部分 Axios 封装都用这种，比 .get() 更常见
_URL_FIELD_RE = re.compile(
    r"""(?<![.\w])url\s*:\s*[`"'](https?://[^`"']+|//[^`"']+|/[^`"']{1,300})[`"']""",
    re.IGNORECASE,
)
_METHOD_FIELD_RE = re.compile(
    r"""\bmethod\s*:\s*[`"'](GET|POST|PUT|DELETE|PATCH)["'`]""", re.IGNORECASE
)

# 声明式接口定义（DSL）：helper(`接口名`, `/api/xxx`, {配置})
# 例：.add(Ue(`status`, `/api/go/status`, {success:..., error:...}))
#      .add(UN(`drawerUsage`, `/drawer/usage`, {success:...}))
# 这类写法的路径是「函数的第 2 个参数」，.get("/x") / fetch("/x") 都抓不到。
# 第 3 个参数要求是 { 或 [（配置对象/数组），以区别于普通的多参函数调用，减少误报。
_DSL_ENDPOINT_RE = re.compile(
    r"""[A-Za-z_$][\w$]*\s*\(\s*[`"'][^`"']{0,80}[`"']\s*,\s*[`"'](/[^`"']{1,200})[`"']\s*,\s*[\[{]"""
)

# 完整 URL 直接写在 JS 里
_ABS_URL_RE = re.compile(r"""["'](https?://[^"'<>\\\s]{8,300})["']""")

# JS 常量：const BASE = "https://..."
_JS_CONST_RE = re.compile(
    r"""(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*["']([^"']{1,300})["']"""
)

# 常量带默认值：var BASE = String(SITE.DATA_BASE || "/data")
# 真实站点常见，不能只认 var BASE = "..."
_JS_CONST_FALLBACK_RE = re.compile(
    r"""(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"""
    r"""[^;\n]{0,160}?\|\|\s*["']([^"']{1,300})["']"""
)

# 拼接：BASE + "/path/"（左拼接最常见）
# 例：getJson(BASE + "/data/author-docs-" + bucket + ".json")
_CONCAT_LEFT_RE = re.compile(
    r"""\b([A-Za-z_$][\w$]*)\s*\+\s*["']([^"']{1,200})["']"""
)

# API 域名：//api.xxx.com/、https://api.xxx.com/、//open.xxx.com/
_API_HOST_RE = re.compile(
    r"""(?:https?:)?//((?:api|open|gateway|gw|interface)[\w.-]*\.[\w.-]+)/""",
    re.IGNORECASE,
)
_FAKE_API_HOSTS = {"api.domain.com", "your-api.com", "api.example.com", "api._domain_"}

# 松散模式：孤立的 "/xxx" 字符串（噪音大，默认关闭）
_LOOSE_PATH_RE = re.compile(r"""["'](/[a-zA-Z][\w\-/]{1,80})["']""")

# JS 加载器里嵌套的 chunk
_NESTED_JS_RE = re.compile(r"""["']([^"']{1,200}\.js(?:\?[^"']*)?)["']""")

_STATIC_EXT = (
    ".js",
    ".mjs",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".map",
    ".mp4",
    ".mp3",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".rar",
    ".7z",
    ".exe",
    ".apk",
    ".dmg",
    ".txt",
)

# 常见占位符 / SDK 默认值，不是真实接口
_PLACEHOLDER_BASE_URLS = {
    "//",
    "http://",
    "https://",
    "//api.domain.com",
    "https://api.domain.com",
    "//your-api.com",
    "https://your-api.com",
}


@dataclass(frozen=True)
class JsFinding:
    """一条发现。kind: base_url / constant / endpoint / field / script。"""

    kind: str
    value: str
    source: str = ""
    resolved: str = ""
    method: str = ""
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "source": self.source,
            "resolved": self.resolved,
            "method": self.method,
            "context": self.context,
        }


@dataclass
class JsHuntResult:
    """一次 JS 包分析的结果。"""

    page_url: str
    scripts: list[str] = field(default_factory=list)
    base_urls: list[JsFinding] = field(default_factory=list)
    endpoints: list[JsFinding] = field(default_factory=list)
    field_hits: list[JsFinding] = field(default_factory=list)
    failed_scripts: list[dict[str, str]] = field(default_factory=list)
    nested_scripts: list[str] = field(default_factory=list)
    api_hosts: list[str] = field(default_factory=list)
    scanned_scripts: int = 0
    source_mode: str = "html"
    warnings: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """已扫描 / 已发现。发现 0 个时返回 0，避免除零。"""
        if not self.scripts:
            return 0.0
        return min(1.0, self.scanned_scripts / len(self.scripts))

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_url": self.page_url,
            "source_mode": self.source_mode,
            "scripts": self.scripts,
            "discovered_scripts": len(self.scripts),
            "scanned_scripts": self.scanned_scripts,
            "coverage": round(self.coverage, 3),
            "nested_scripts": self.nested_scripts,
            "api_hosts": self.api_hosts,
            "base_urls": [f.to_dict() for f in self.base_urls],
            "endpoints": [f.to_dict() for f in self.endpoints],
            "field_hits": [f.to_dict() for f in self.field_hits],
            "failed_scripts": self.failed_scripts,
            "warnings": self.warnings,
        }

    def summary(self) -> str:
        lines = [
            f"页面: {self.page_url}",
            f"脚本: 发现 {len(self.scripts)} 个，扫描 {self.scanned_scripts} 个"
            f"（覆盖率 {self.coverage:.0%}，来源 {self.source_mode}）",
            f"baseURL: {sum(1 for f in self.base_urls if f.kind == 'base_url')} 个",
            f"接口候选: {len(self.endpoints)} 个",
            f"字段命中: {len(self.field_hits)} 个",
        ]
        constants = [f for f in self.base_urls if f.kind == "constant"]
        if constants:
            lines.append(f"JS 常量: {len(constants)} 个")
        if self.nested_scripts:
            lines.append(f"动态 chunk: {len(self.nested_scripts)} 个")
        if self.failed_scripts:
            lines.append(f"下载失败: {len(self.failed_scripts)} 个")
        if self.api_hosts:
            lines.append(
                f"API 域名: {', '.join(self.api_hosts)}"
                "（候选接口若 404，用 --api-host 重拼）"
            )
        for warning in self.warnings:
            lines.append(f"[!] {warning}")
        return "\n".join(lines)


# ── 网络 ────────────────────────────────────────────────
def _http_get(
    url: str,
    *,
    referer: str = "",
    cookies: dict[str, str] | None = None,
    timeout: int = 15,
) -> tuple[int, str, str]:
    """GET 文本，返回 (status, text, error)。

    优先 AntiRiskHttpClient（curl_cffi TLS 指纹 + 统一风险分类），
    失败回退 urllib。防盗链（403）通常补 Referer 就能过。
    """
    headers: dict[str, str] = {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer

    try:
        from .http import AntiRiskHttpClient

        client = AntiRiskHttpClient("js_bundle", timeout=timeout, max_retries=1)
        result = client.get_text(url, headers=headers, cookies=cookies or None)
        if result.status_code == 0:
            # 网络层失败：get_text 把错误信息放在 text 里，不能当正文
            return 0, "", result.text or "request failed"
        return result.status_code, result.text or "", ""
    except Exception as exc:
        logger.debug("AntiRiskHttpClient 请求失败，回退 urllib: %r", exc)

    try:
        import urllib.error
        import urllib.request

        fallback_headers = {"User-Agent": DEFAULT_UA, **headers}
        cookie_header = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())
        if cookie_header:
            fallback_headers["Cookie"] = cookie_header
        req = urllib.request.Request(url, headers=fallback_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.status, raw.decode(charset, errors="replace"), ""
    except urllib.error.HTTPError as exc:
        return exc.code, "", f"HTTP {exc.code}"
    except Exception as exc:
        return 0, "", repr(exc)


# ── 提取 ────────────────────────────────────────────────
def extract_script_urls(html: str, page_url: str) -> list[str]:
    """从 HTML 里提取所有脚本入口，转成绝对 URL 并去重。

    除了 ``<script src>``，还会读 ``<link rel="modulepreload">`` /
    ``<link rel="preload" as="script">``。Vite 等构建工具常把入口
    chunk 放在这里，只读 script 标签会漏掉整棵动态 import 树。
    """
    urls: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        raw = (raw or "").strip()
        if not raw or raw.startswith("data:") or raw.startswith("blob:"):
            return
        absolute = urljoin(page_url, raw)
        if absolute in seen:
            return
        seen.add(absolute)
        urls.append(absolute)

    for match in _SCRIPT_SRC_RE.finditer(html or ""):
        add(match.group(1) or "")

    for match in _LINK_TAG_RE.finditer(html or ""):
        tag = match.group(0)
        rel_match = _LINK_REL_RE.search(tag)
        if not rel_match:
            continue
        rel = (rel_match.group(1) or "").lower()
        if "modulepreload" not in rel:
            as_match = _LINK_AS_RE.search(tag)
            as_value = (as_match.group(1) or "").lower() if as_match else ""
            if not (rel == "preload" and as_value == "script"):
                continue
        href_match = _LINK_HREF_RE.search(tag)
        if href_match:
            add(href_match.group(1) or "")

    return urls


def extract_nested_scripts(js: str, source_url: str, *, limit: int = 12) -> list[str]:
    """从任意 JS 里提取它引用的二级 chunk（深度 1）。

    旧实现只对小于 3KB 的「加载器」调用，并且只认 chunk/main/index/app/
    vendor 等关键字。真实站点（Vite / Rollup）的动态 chunk 往往叫
    ``profile-Cgkt1CT6.js`` / ``workbench-Dojrq00F.js``，名字里没有这些
    关键字，于是整棵 import 树被漏掉。

    现在：任何 JS 都可以调用；只要是 ``.js`` 相对/绝对路径就收，
    由调用方用 ``max_scripts`` 控制总量。
    """
    urls: list[str] = []
    seen: set[str] = set()
    for match in _NESTED_JS_RE.finditer(js or ""):
        raw = (match.group(1) or "").strip()
        if not raw or raw.startswith(("data:", "blob:", "javascript:")):
            continue
        lowered = raw.lower().split("?", 1)[0]
        if lowered.endswith((".js.map", ".mjs.map")):
            continue
        # static/、assets/、js/ 开头的路径通常是相对站点根，不是相对当前 JS 目录
        if lowered.startswith(("static/", "assets/", "js/")):
            absolute = _origin(source_url) + "/" + raw.lstrip("/")
        else:
            absolute = urljoin(source_url, raw)
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute == source_url or absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
        if len(urls) >= limit:
            break
    return urls


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _is_static(path: str) -> bool:
    lowered = (path or "").lower().split("?", 1)[0]
    return lowered.endswith(_STATIC_EXT)


def _is_plausible_base_url(value: str) -> bool:
    """过滤 baseURL="https://".concat(...) 这类拼接片段。"""
    if not value or value in _PLACEHOLDER_BASE_URLS:
        return False
    if len(value) < 3 or value.endswith("://"):
        return False
    if value.startswith("//") and len(value) <= 2:
        return False
    return True


def extract_api_hosts(js: str) -> list[str]:
    """从 JS 里提取 API 域名，如 api.bilibili.com。

    很多 SDK 用相对路径 ``/x/...`` + 默认 API 域名，
    只按页面 origin 拼会 404，需要这个提示。
    """
    hosts: list[str] = []
    for match in _API_HOST_RE.finditer(js or ""):
        host = (match.group(1) or "").lower().strip(".")
        if not host or host in _FAKE_API_HOSTS:
            continue
        if host not in hosts:
            hosts.append(host)
    return hosts


_NOISE_URL_PREFIXES = (
    "http://www.w3.org/",
    "https://www.w3.org/",
    "http://www.w3.org/1999/",
    "https://www.w3.org/1999/",
)


def _looks_like_url_or_path(value: str) -> bool:
    """过滤掉包名、XML 命名空间、静态脚本等噪音。"""
    text = (value or "").strip()
    if len(text) < 3:
        return False
    lowered = text.lower()
    if lowered.startswith(_NOISE_URL_PREFIXES):
        return False
    if _is_static(text):
        return False
    if text.startswith(("http://", "https://", "//")):
        # 带 query/fragment 的是具体页面链接，不是 base URL
        if "?" in text or "#" in text:
            return False
        return True
    if text.startswith("/"):
        return len(text) >= 3
    # 其余（base64 字符表、npm 包名、普通字符串）一律不当常量
    return False


def extract_js_constants(js: str) -> dict[str, str]:
    """提取 JS 里「像 URL/路径」的常量。

    真实站点很少写 ``fetch("/api/list")``，更常见的是::

        var BASE = "https://www.example.com";
        getJson(BASE + "/data/list")
        fetch(API + "/api/v2/" + name)

    只认字面量路径的正则会漏掉这些。本函数先建常量表，
    供后面的「常量 + 字符串」拼接解析使用。
    """
    constants: dict[str, str] = {}
    for regex in (_JS_CONST_RE, _JS_CONST_FALLBACK_RE):
        for match in regex.finditer(js or ""):
            name = (match.group(1) or "").strip()
            value = (match.group(2) or "").strip()
            if not name or not _looks_like_url_or_path(value):
                continue
            # 同名只保留第一次：后面的可能是运行时覆盖
            constants.setdefault(name, value)
    return constants


def _resolve_concat(
    prefix_value: str, suffix: str, page_url: str, *, api_host: str = ""
) -> str:
    """把「常量 + 字符串」拼成完整 URL。"""
    if not prefix_value or not suffix:
        return ""
    if prefix_value.startswith("http://") or prefix_value.startswith("https://"):
        base = prefix_value.rstrip("/")
    elif prefix_value.startswith("//"):
        base = "https:" + prefix_value.rstrip("/")
    elif prefix_value.startswith("/"):
        origin = f"https://{api_host}" if api_host else _origin(page_url)
        base = origin + prefix_value.rstrip("/")
    else:
        base = urljoin(page_url, prefix_value).rstrip("/")
    return base + "/" + suffix.lstrip("/")


def _resolve(
    base_url: str | None, page_url: str, path: str, *, api_host: str = ""
) -> str:
    """把 baseURL + 接口路径拼成完整 URL。"""
    if not path or path in ("//", "/", "#"):
        return ""
    if path.startswith("//"):
        return "https:" + path
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if base_url and not _is_plausible_base_url(base_url):
        base_url = None
    origin = f"https://{api_host}" if api_host else _origin(page_url)
    if base_url:
        if base_url.startswith("http://") or base_url.startswith("https://"):
            base = base_url.rstrip("/")
        elif base_url.startswith("/"):
            base = origin + base_url.rstrip("/")
        else:
            base = urljoin(page_url, base_url).rstrip("/")
        return base + "/" + path.lstrip("/")
    return origin + "/" + path.lstrip("/")


def _endpoint_priority(finding: JsFinding) -> tuple[int, int]:
    """把更像接口的排前面：/api/、/x/、/v1/ 优先，空 resolved 排最后。"""
    path = (finding.resolved or "").lower()
    score = 0
    if "/api/" in path:
        score -= 4
    if "/x/" in path:
        score -= 3
    if "/v1/" in path or "/v2/" in path or "/v3/" in path:
        score -= 2
    if "/graphql" in path:
        score -= 3
    if not path:
        score += 10
    return (score, len(path))


def _context(text: str, start: int, end: int, *, window: int = 110) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    snippet = text[left:right].replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", snippet).strip()


def analyze_js(
    js: str,
    *,
    source: str,
    page_url: str,
    base_urls: list[str] | None = None,
    fields: Iterable[str] = (),
    loose: bool = False,
    api_host: str = "",
) -> tuple[list[JsFinding], list[JsFinding], list[JsFinding]]:
    """分析单个 JS，返回 (base_url_findings, endpoint_findings, field_findings)。"""
    base_urls = base_urls or []
    base_findings: list[JsFinding] = []
    endpoint_findings: list[JsFinding] = []
    field_findings: list[JsFinding] = []

    # 1. baseURL
    for match in _BASE_URL_RE.finditer(js or ""):
        value = (match.group(1) or "").strip()
        if not _is_plausible_base_url(value):
            continue
        base_findings.append(
            JsFinding(
                kind="base_url",
                value=value,
                source=source,
                resolved=(
                    _resolve(None, page_url, value, api_host=api_host)
                    if value.startswith("/")
                    else value
                ),
                context=_context(js, match.start(), match.end()),
            )
        )
        if value not in base_urls:
            base_urls.append(value)

    # 1.5 JS 常量 + 拼接（BASE + "/api/"）
    # 真实站点很少写字面量路径，更多是「常量 + 字符串」
    constants = extract_js_constants(js or "")
    for name, value in constants.items():
        idx = (js or "").find(name)
        base_findings.append(
            JsFinding(
                kind="constant",
                value=f"{name} = {value}",
                source=source,
                resolved=value if value.startswith(("http", "//")) else "",
                context=_context(js, idx, idx + len(name)) if idx >= 0 else "",
            )
        )

    seen: set[tuple[str, str]] = set()
    for match in _CONCAT_LEFT_RE.finditer(js or ""):
        ident = (match.group(1) or "").strip()
        suffix = (match.group(2) or "").strip()
        if ident not in constants:
            continue
        resolved = _resolve_concat(
            constants[ident], suffix, page_url, api_host=api_host
        )
        if not resolved:
            continue
        key = ("", resolved)
        if key in seen:
            continue
        seen.add(key)
        endpoint_findings.append(
            JsFinding(
                kind="endpoint",
                value=f"{ident} + {suffix!r}",
                source=source,
                resolved=resolved,
                method="",
                context=_context(js, match.start(), match.end()),
            )
        )

    # 2. 接口路径（.get / .post / fetch / axios）
    for regex, default_method in ((_HTTP_VERB_RE, ""), (_FETCH_RE, "GET")):
        for match in regex.finditer(js or ""):
            if regex is _HTTP_VERB_RE:
                method = (match.group(1) or "").upper()
                path = (match.group(2) or "").strip()
            else:
                method = default_method
                path = (match.group(1) or "").strip()
            if not path or path in ("//", "/", "#") or _is_static(path):
                continue
            resolved = _resolve(
                base_urls[-1] if base_urls else None,
                page_url,
                path,
                api_host=api_host,
            )
            key = (method, resolved)
            if key in seen:
                continue
            seen.add(key)
            endpoint_findings.append(
                JsFinding(
                    kind="endpoint",
                    value=path,
                    source=source,
                    resolved=resolved,
                    method=method,
                    context=_context(js, match.start(), match.end()),
                )
            )

    # 2.5 对象字面量：{url:"/x/...", method:"GET"}
    # B站、部分 Axios 封装用这种写法，不是 .get()
    for match in _URL_FIELD_RE.finditer(js or ""):
        path = (match.group(1) or "").strip()
        if not path or path in ("//", "/", "#") or _is_static(path):
            continue
        # method 可能在 url 前面（{method:"POST",url:"..."}），也可能在后面
        text = js or ""
        window = text[max(0, match.start() - 200) : match.end() + 200]
        method_match = _METHOD_FIELD_RE.search(window)
        method = method_match.group(1).upper() if method_match else ""
        resolved = _resolve(
            base_urls[-1] if base_urls else None, page_url, path, api_host=api_host
        )
        key = (method, resolved)
        if key in seen:
            continue
        seen.add(key)
        endpoint_findings.append(
            JsFinding(
                kind="endpoint",
                value=path,
                source=source,
                resolved=resolved,
                method=method,
                context=_context(js, match.start(), match.end()),
            )
        )

    # 2.6 声明式接口定义（DSL）：helper(`接口名`, `/api/xxx`, {配置})
    # 例：.add(Ue(`status`, `/api/go/status`, {success:..., error:...}))
    #      .add(UN(`drawerUsage`, `/drawer/usage`, {success:...}))
    for match in _DSL_ENDPOINT_RE.finditer(js or ""):
        path = (match.group(1) or "").strip()
        if not path or path in ("//", "/", "#") or _is_static(path):
            continue
        if any(ch in path for ch in (" ", "<", ">", "\\")):
            continue
        # 单段路径（如 /foo）噪音大，至少两段才当接口候选
        if path.count("/") < 2:
            continue
        # 已含 /api 的路径不要再叠加 baseURL，避免拼出 /api/api/...
        base_for_path = (
            None if path.startswith("/api/")
            else (base_urls[-1] if base_urls else None)
        )
        resolved = _resolve(base_for_path, page_url, path, api_host=api_host)
        key = ("", resolved)
        if key in seen:
            continue
        seen.add(key)
        endpoint_findings.append(
            JsFinding(
                kind="endpoint",
                value=path,
                source=source,
                resolved=resolved,
                method="",
                context=_context(js, match.start(), match.end()),
            )
        )

    # 3. 完整 URL
    for match in _ABS_URL_RE.finditer(js or ""):
        raw = (match.group(1) or "").strip()
        if _is_static(raw) or "/api/" not in raw.lower() and "/v1/" not in raw.lower():
            continue
        key = ("", raw)
        if key in seen:
            continue
        seen.add(key)
        endpoint_findings.append(
            JsFinding(
                kind="endpoint",
                value=raw,
                source=source,
                resolved=raw,
                context=_context(js, match.start(), match.end()),
            )
        )

    # 4. 松散模式：孤立的 "/xxx" 字符串
    if loose:
        for match in _LOOSE_PATH_RE.finditer(js or ""):
            path = (match.group(1) or "").strip()
            if _is_static(path) or len(path) < 4:
                continue
            resolved = _resolve(
            base_urls[-1] if base_urls else None, page_url, path, api_host=api_host
        )
            key = ("", resolved)
            if key in seen:
                continue
            seen.add(key)
            endpoint_findings.append(
                JsFinding(
                    kind="endpoint",
                    value=path,
                    source=source,
                    resolved=resolved,
                    context=_context(js, match.start(), match.end()),
                )
            )

    # 5. 字段名
    for name in fields:
        if not name:
            continue
        start = 0
        hits = 0
        while hits < 3:
            idx = (js or "").find(name, start)
            if idx < 0:
                break
            field_findings.append(
                JsFinding(
                    kind="field",
                    value=name,
                    source=source,
                    context=_context(js, idx, idx + len(name), window=140),
                )
            )
            hits += 1
            start = idx + len(name)

    return base_findings, endpoint_findings, field_findings


def hunt_api(
    page_url: str,
    *,
    html: str | None = None,
    cookies: dict[str, str] | None = None,
    fields: Iterable[str] = (),
    referer: str = "",
    max_scripts: int = 80,
    timeout: int = 15,
    fetch: bool = True,
    loose: bool = False,
    follow_loaders: bool = True,
    api_host: str = "",
    extra_scripts: Iterable[str] = (),
    source_mode: str = "html",
    progress: Callable[[int, int, str], None] | None = None,
) -> JsHuntResult:
    """完整流程：HTML → script 列表 → 下载 JS → 找 baseURL / 接口 / 字段。

    Args:
        page_url: 页面 URL（用于拼相对路径、做默认 Referer）。
        html: 已有 HTML 时直接传，省一次请求。
        cookies: 登录 cookie（dict）。接口需要登录时必传。
        fields: 想在 JS 里定位的字段名，如 ``["fansNum", "subscribeNum"]``。
        referer: 下载 JS 时的 Referer，默认用 page_url（绕防盗链）。
        max_scripts: 最多下载多少个 JS（默认 80，覆盖 SPA 的全部 chunk）。
        fetch: False 时只分析 HTML 里的 script URL，不下载。
        loose: 松散模式，把孤立的 ``"/xxx"`` 也当候选（噪音大）。
        follow_loaders: 对任意 JS 继续抓它引用的 chunk（深度 1）。
        extra_scripts: 额外入口，通常是浏览器 Network 里枚举到的 JS URL。
            用于补 HTML 里看不到的动态 import / modulepreload。
        source_mode: 记录脚本来源（html / browser / html+browser）。
        progress: 可选进度回调 ``(已扫描, 总数, 当前URL)``，用于长任务输出。
    """
    result = JsHuntResult(page_url=page_url, source_mode=source_mode)
    referer = referer or page_url

    if html is None:
        status, html, error = _http_get(page_url, referer=referer, cookies=cookies, timeout=timeout)
        if not html:
            result.failed_scripts.append(
                {"url": page_url, "status": str(status), "error": error or "empty html"}
            )
            result.warnings.append(
                "页面 HTML 抓取失败；若浏览器能打开，改用 --browser always 枚举 JS。"
            )
            return result

    result.scripts = extract_script_urls(html, page_url)
    extra = [str(item).strip() for item in extra_scripts if str(item).strip()]
    if extra:
        for item in extra:
            if item not in result.scripts:
                result.scripts.append(item)
        result.source_mode = "html+browser" if html else "browser"

    if not fetch or not result.scripts:
        if not result.scripts:
            result.warnings.append(
                "未发现任何 script：可能是 SPA 动态加载，或 HTML 被风控替换。"
                "建议加 --browser always 枚举页面实际加载的 JS。"
            )
        return result

    # 下载（先下前 max_scripts 个；任意 JS 里引用的 chunk 再追加）
    queue = list(result.scripts[:max_scripts])
    js_texts: list[tuple[str, str]] = []
    fetched: set[str] = set()

    while queue:
        src = queue.pop(0)
        if src in fetched:
            continue
        fetched.add(src)
        status, text, error = _http_get(src, referer=referer, cookies=cookies, timeout=timeout)
        if not text:
            result.failed_scripts.append(
                {"url": src, "status": str(status), "error": error or "empty body"}
            )
            continue
        js_texts.append((src, text))
        if progress is not None:
            progress(len(js_texts), max(len(result.scripts), len(js_texts)), src)

        # 任意 JS 都可能引用动态 chunk，不再只对「短小加载器」跟进
        if follow_loaders and len(js_texts) < max_scripts:
            for nested in extract_nested_scripts(text, src):
                if nested not in result.nested_scripts:
                    result.nested_scripts.append(nested)
                if nested not in result.scripts:
                    result.scripts.append(nested)
                if (
                    nested not in fetched
                    and nested not in queue
                    and len(js_texts) < max_scripts
                ):
                    queue.append(nested)

    result.scanned_scripts = len(js_texts)
    if progress is not None:
        progress(
            result.scanned_scripts,
            max(len(result.scripts), result.scanned_scripts),
            "",
        )
    if result.scanned_scripts < len(result.scripts):
        result.warnings.append(
            f"只扫描了 {result.scanned_scripts}/{len(result.scripts)} 个 JS"
            f"（max_scripts={max_scripts}）；提高上限可补全。"
        )
    if len(result.scripts) <= 2:
        result.warnings.append(
            "只发现 ≤2 个 JS，疑似 SPA 壳或动态 import；"
            "建议加 --browser always 枚举页面实际加载的全部 JS。"
        )

    # 第一遍：收集所有 baseURL（可能定义在别的 chunk 里）
    all_base_urls: list[str] = []
    for src, text in js_texts:
        for host in extract_api_hosts(text):
            if host not in result.api_hosts:
                result.api_hosts.append(host)
        base_findings, _, _ = analyze_js(
            text,
            source=src,
            page_url=page_url,
            fields=(),
            loose=False,
            api_host=api_host,
        )
        for finding in base_findings:
            result.base_urls.append(finding)
            # 常量只用于展示，不参与 baseURL 拼接（避免把 API 域名当站点根）
            if finding.kind != "base_url":
                continue
            if finding.value not in all_base_urls:
                all_base_urls.append(finding.value)

    # 第二遍：用收集到的 baseURL 拼接口 + 找字段
    seen_endpoints: set[tuple[str, str]] = set()
    for src, text in js_texts:
        _, endpoints, field_hits = analyze_js(
            text,
            source=src,
            page_url=page_url,
            base_urls=all_base_urls,
            fields=fields,
            loose=loose,
            api_host=api_host,
        )
        for ep in endpoints:
            key = (ep.method, ep.resolved)
            if key in seen_endpoints:
                continue
            seen_endpoints.add(key)
            result.endpoints.append(ep)
        result.field_hits.extend(field_hits)

    result.endpoints.sort(key=_endpoint_priority)

    logger.info(
        "[js-hunt] %s: %d scripts, %d baseURL, %d endpoints, %d field hits",
        page_url,
        len(js_texts),
        len(result.base_urls),
        len(result.endpoints),
        len(result.field_hits),
    )
    return result


# 公开别名：工具层复用同一套请求逻辑（带 Referer / TLS 指纹 / 回退）
fetch_text = _http_get


def parse_cookie_header(raw: str) -> dict[str, str]:
    """把 ``"a=1; b=2"`` 解析成 dict。"""
    cookies: dict[str, str] = {}
    for part in (raw or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            cookies[key] = value.strip()
    return cookies
