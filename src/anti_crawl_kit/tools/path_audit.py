"""平台路径全量审计 —— 把 docs/23 的 14 条路径矩阵变成可执行跑批。

目标
----
对平台 X **测所有路径，保存方法，选出生产使用的最优方法**。

这个工具解决三个问题：

1. 不再靠记忆判断「哪条路能用」——每条路径都有 status / 耗时 / 登录 /
   签名 / 字段口径 / 证据；
2. 没测过的路径明确写 ``not_tested``，**不写「不可用」**；
3. 单次探测**不冒充生产验收**——评分卡只做初筛，成功率 ≥95% 与
   连续 3 天稳定性必须另外补实验。

用法::

    # 1) 写平台配置（见 examples/platforms/bilibili_audit.yaml）
    python tools/path_audit.py --config examples/platforms/bilibili_audit.yaml

    # 2) 只跑几条路
    python tools/path_audit.py --config ... --only anonymous_json,ssr_state

    # 3) 允许启动浏览器（默认 auto：装了 patchright 才跑浏览器路径）
    python tools/path_audit.py --config ... --browser always --headed

    # 4) 输出到指定目录
    python tools/path_audit.py --config ... --out runs/20261108-bilibili

退出码::

    0  审计完成（结论可能是「没有可用路径」，这不是执行失败）
    1  配置 / 参数错误
    2  验证墙或明确封禁，按红线硬停（截图 + 通知 + 不再继续）

设计约束
--------
- 不内置任何签名算法；需要签名的接口用 ``signer`` 契约或走浏览器 XHR；
- 不把 Cookie 写进输出文件；
- HTTP 请求一律走 :class:`anti_crawl_kit.http.AntiRiskHttpClient`；
- 浏览器路径惰性导入 patchright，没装也能跑纯 HTTP 审计。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ..empty_shell import detect_empty_shell
from ..http_signed import SignedEndpoint, classify_signed_response
from ..page_shape import classify_page_shape
from ..run_report import RunReport
from ..state_extract import extract_state, pick_path

# ── 路径目录（与 docs/23 第 1 节一一对应）─────────────


@dataclass(frozen=True)
class PathSpec:
    """一条待审计路径的静态描述。"""

    key: str
    index: int
    label: str
    layer: str
    # data: 直接产出字段；discovery: 只发现接口，不产出字段
    role: str = "data"
    # 未实现 / 未配置时给使用者的「怎么测」
    how_to_test: str = ""


PATH_CATALOG: tuple[PathSpec, ...] = (
    PathSpec(
        "anonymous_html", 1, "匿名 HTML（SSR）", "L2",
        how_to_test="直接 GET test_url；看 page_shape 是否 ssr/ssr_state。",
    ),
    PathSpec(
        "anonymous_json", 2, "匿名 JSON API / REST", "L1",
        how_to_test="在 endpoints 里声明 path: anonymous_json 的接口。",
    ),
    PathSpec(
        "anonymous_graphql", 3, "匿名 GraphQL", "L1",
        how_to_test=(
            "在 endpoints 里声明 path: anonymous_graphql，"
            "method: POST，json_data 填 query/variables。"
        ),
    ),
    PathSpec(
        "mobile_api", 4, "旧版 / 移动端 API", "L1",
        how_to_test="在 endpoints 里声明 path: mobile_api，补移动端 UA / Referer。",
    ),
    PathSpec(
        "share_ssr", 5, "分享页 / 公开主页 SSR", "L2",
        how_to_test="配置 share_url（分享页或公开主页），再跑本条。",
    ),
    PathSpec(
        "ssr_state", 6, "SSR 状态容器", "L2",
        how_to_test=(
            "配置 state_fields（字段名 → 状态 JSON 路径），"
            "必要时配置 state_path 统计列表条数。"
        ),
    ),
    PathSpec(
        "js_bundle", 7, "JS 包找接口（含动态 chunk）", "L0",
        role="discovery",
        how_to_test="自动扫描页面全部 JS；动态 chunk 多时加 --browser always。",
    ),
    PathSpec(
        "login_http", 8, "登录态 HTTP（Cookie）", "L1",
        how_to_test=(
            "配置 cookies 或 cookies_env；"
            "endpoints 里声明 path: login_http。"
        ),
    ),
    PathSpec(
        "browser_xhr", 9, "浏览器 XHR 拦截", "L3",
        how_to_test="配置 xhr_pattern 和 xhr_fields；需要 [browser] extra。",
    ),
    PathSpec(
        "signature_replay", 10, "浏览器导出签名参数 → HTTP 重放", "L1（伪）",
        how_to_test=(
            "从浏览器导出一次完整请求（URL / headers / cookies / 签名参数），"
            "用 endpoints 声明 path: signature_replay；跑 10 个目标测 TTL。"
        ),
    ),
    PathSpec(
        "open_platform", 11, "官方开放平台 / 合作 API", "L1",
        how_to_test=(
            "申请官方开放平台权限，配置 OAuth / AppKey 后作为独立 endpoint 接入；"
            "本工具不绕过审批。"
        ),
    ),
    PathSpec(
        "app_api", 12, "App API / 私有协议", "L1",
        how_to_test=(
            "抓包 App 请求，记录设备指纹与签名契约；"
            "只采公开可见数据，遵守平台条款。"
        ),
    ),
    PathSpec(
        "dom", 13, "DOM 解析", "L4",
        how_to_test="配置 card_selector / dom_fields；需要 [browser] extra。",
    ),
    PathSpec(
        "ocr", 14, "截图 / OCR", "L4",
        how_to_test="截图后用 OCR 识别；需额外 OCR 依赖，只做最后兜底。",
    ),
)

PATHS: dict[str, PathSpec] = {spec.key: spec for spec in PATH_CATALOG}

# docs/23 第 2 节的 stop_reason 取值，额外补 not_tested
AUDIT_STOP_REASONS = (
    "completed",
    "login_required",
    "blocked",
    "rate_limited",
    "captcha",
    "signature_failed",
    "parameter_error",
    "network",
    "not_found",
    "empty",
    "not_tested",
)

_RISK_TO_AUDIT = {
    "ok": "completed",
    "login_required": "login_required",
    "captcha": "captcha",
    "rate_limited": "rate_limited",
    "blocked": "blocked",
    "signature_failed": "signature_failed",
    "network_error": "network",
}

_SIGNED_TO_AUDIT = {
    "ok": "completed",
    "login_required": "login_required",
    "captcha": "captcha",
    "rate_limited": "rate_limited",
    "signature_failed": "signature_failed",
    "param_error": "parameter_error",
    "not_found": "not_found",
    "empty": "empty",
    "network": "network",
}

_SHAPE_TO_AUDIT = {
    "ssr": "completed",
    "ssr_state": "completed",
    "spa_shell": "empty",
    "login_wall": "login_required",
    "captcha_wall": "captcha",
    "blocked": "blocked",
    "tiny": "empty",
    "unknown": "completed",
}

# 遇到这些必须硬停，绝不换路径硬试
HARD_STOP_REASONS = frozenset({"captcha", "blocked"})


def _today() -> str:
    return date.today().isoformat()


def _snippet(text: str, limit: int = 160) -> str:
    return " ".join((text or "").split())[:limit]


def _parse_cookie_header(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in (raw or "").split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key:
            cookies[key.strip()] = value.strip()
    return cookies


def _render_mapping(data: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    """递归替换 ``{uid}`` 这类占位符。"""
    rendered: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            try:
                rendered[key] = value.format(**context)
            except (KeyError, IndexError):
                rendered[key] = value
        elif isinstance(value, dict):
            rendered[key] = _render_mapping(value, context)
        elif isinstance(value, list):
            rendered[key] = [
                item.format(**context) if isinstance(item, str) else item
                for item in value
            ]
        else:
            rendered[key] = value
    return rendered


# ── 结果 ────────────────────────────────────────────


@dataclass
class PathResult:
    """一条路径的审计结果，字段对齐 docs/23 第 2 节模板。"""

    platform: str
    test_url: str
    path: str
    layer: str
    label: str = ""
    status: int = 0
    latency_ms: int = 0
    login_required: bool = False
    signature_required: bool = False
    fields: dict[str, Any] = field(default_factory=dict)
    field_semantics: dict[str, str] = field(default_factory=dict)
    stop_reason: str = "not_tested"
    evidence: str = ""
    tested_at: str = ""
    tested: bool = False
    note: str = ""
    coverage: dict[str, Any] = field(default_factory=dict)
    # 评分卡
    scores: dict[str, int] = field(default_factory=dict)
    total_score: int = 0
    rejected: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "test_url": self.test_url,
            "path": self.path,
            "layer": self.layer,
            "label": self.label,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "login_required": self.login_required,
            "signature_required": self.signature_required,
            "fields": self.fields,
            "field_semantics": self.field_semantics,
            "stop_reason": self.stop_reason,
            "evidence": self.evidence,
            "tested_at": self.tested_at,
            "tested": self.tested,
            "note": self.note,
            "coverage": self.coverage,
            "scores": self.scores,
            "total_score": self.total_score,
            "rejected": self.rejected,
        }


def _base_result(config: "AuditConfig", spec: PathSpec) -> PathResult:
    return PathResult(
        platform=config.platform,
        test_url=config.test_url,
        path=spec.key,
        layer=spec.layer,
        label=spec.label,
        tested_at=_today(),
    )


def _not_tested(
    config: "AuditConfig", spec: PathSpec, note: str
) -> PathResult:
    result = _base_result(config, spec)
    result.stop_reason = "not_tested"
    result.note = note
    return result


# ── 配置 ────────────────────────────────────────────


@dataclass(frozen=True)
class DomField:
    """DOM 字段规则：选择器 + 读取方式。"""

    selector: str
    attr: str = "text"


@dataclass
class AuditEndpoint:
    """一条待测接口的声明（比 http_signed 多审计需要的元数据）。"""

    name: str
    url: str
    path: str = "anonymous_json"
    method: str = "GET"
    params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    json_data: dict[str, Any] = field(default_factory=dict)
    signer: str = ""
    risk_codes: dict[str, str] = field(default_factory=dict)
    required_fields: tuple[str, ...] = ()
    # 字段名 → JSON 路径，例如 followers: data.follower
    fields: dict[str, str] = field(default_factory=dict)
    field_semantics: dict[str, str] = field(default_factory=dict)
    login_required: bool = False
    signature_required: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEndpoint":
        return cls(
            name=str(data.get("name") or data.get("url") or "endpoint"),
            url=str(data["url"]),
            path=str(data.get("path") or "anonymous_json"),
            method=str(data.get("method") or "GET").upper(),
            params={str(k): str(v) for k, v in (data.get("params") or {}).items()},
            headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
            json_data=dict(data.get("json_data") or {}),
            signer=str(data.get("signer") or ""),
            risk_codes={
                str(k): str(v) for k, v in (data.get("risk_codes") or {}).items()
            },
            required_fields=tuple(data.get("required_fields") or ()),
            fields={str(k): str(v) for k, v in (data.get("fields") or {}).items()},
            field_semantics={
                str(k): str(v)
                for k, v in (data.get("field_semantics") or {}).items()
            },
            login_required=bool(data.get("login_required", False)),
            signature_required=bool(data.get("signature_required", False)),
        )


@dataclass
class AuditConfig:
    """平台审计配置。可从 YAML / JSON 加载。"""

    platform: str
    test_url: str
    uid: str = ""
    share_url: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    cookies_env: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    endpoints: list[AuditEndpoint] = field(default_factory=list)
    field_semantics: dict[str, str] = field(default_factory=dict)
    # SSR 状态
    state_path: str = ""
    state_fields: dict[str, str] = field(default_factory=dict)
    state_use_cookies: bool = False
    # 匿名 HTML
    html_use_cookies: bool = False
    # DOM
    card_selector: str = ""
    dom_fields: dict[str, DomField] = field(default_factory=dict)
    # 浏览器 XHR
    xhr_pattern: str = ""
    xhr_fields: dict[str, str] = field(default_factory=dict)
    # JS 包
    js_fields: list[str] = field(default_factory=list)
    max_scripts: int = 80
    # 浏览器
    headless: bool = True
    channel: str = ""
    data_dir: str = ""
    skip_version_check: bool = False
    browser_wait_ms: int = 4000
    xhr_wait_ms: int = 4000
    # 其他
    timeout: int = 20
    max_items: int = 5
    login_required: bool = False
    signature_required: bool = False
    score_overrides: dict[str, dict[str, int]] = field(default_factory=dict)
    # 运行期字段，不序列化
    out_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.cookies_env and not self.cookies:
            raw = os.environ.get(self.cookies_env, "").strip()
            if raw:
                self.cookies = _parse_cookie_header(raw)

    def endpoints_for(self, path: str) -> list[AuditEndpoint]:
        return [endpoint for endpoint in self.endpoints if endpoint.path == path]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditConfig":
        browser = dict(data.get("browser") or {})
        config = cls(
            platform=str(data.get("platform") or "unknown"),
            test_url=str(data.get("test_url") or ""),
            uid=str(data.get("uid") or ""),
            share_url=str(data.get("share_url") or ""),
            cookies={str(k): str(v) for k, v in (data.get("cookies") or {}).items()},
            cookies_env=str(data.get("cookies_env") or ""),
            headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
            endpoints=[
                AuditEndpoint.from_dict(item) for item in (data.get("endpoints") or [])
            ],
            field_semantics={
                str(k): str(v)
                for k, v in (data.get("field_semantics") or {}).items()
            },
            state_path=str(data.get("state_path") or ""),
            state_fields={
                str(k): str(v) for k, v in (data.get("state_fields") or {}).items()
            },
            state_use_cookies=bool(data.get("state_use_cookies", False)),
            html_use_cookies=bool(data.get("html_use_cookies", False)),
            card_selector=str(data.get("card_selector") or ""),
            dom_fields={
                str(k): DomField(
                    selector=str(v.get("selector") or ""),
                    attr=str(v.get("attr") or "text"),
                )
                for k, v in (data.get("dom_fields") or {}).items()
            },
            xhr_pattern=str(data.get("xhr_pattern") or ""),
            xhr_fields={
                str(k): str(v) for k, v in (data.get("xhr_fields") or {}).items()
            },
            js_fields=[str(item) for item in (data.get("js_fields") or [])],
            max_scripts=int(data.get("max_scripts") or 80),
            headless=bool(browser.get("headless", data.get("headless", True))),
            channel=str(browser.get("channel") or data.get("channel") or ""),
            data_dir=str(browser.get("data_dir") or data.get("data_dir") or ""),
            skip_version_check=bool(
                browser.get("skip_version_check", data.get("skip_version_check", False))
            ),
            browser_wait_ms=int(
                browser.get("wait_ms", data.get("browser_wait_ms", 4000))
            ),
            xhr_wait_ms=int(browser.get("xhr_wait_ms", data.get("xhr_wait_ms", 4000))),
            timeout=int(data.get("timeout") or 20),
            max_items=int(data.get("max_items") or 5),
            login_required=bool(data.get("login_required", False)),
            signature_required=bool(data.get("signature_required", False)),
            score_overrides={
                str(k): {str(dim): int(score) for dim, score in (v or {}).items()}
                for k, v in (data.get("score_overrides") or {}).items()
            },
        )
        return config

    def to_dict(self) -> dict[str, Any]:
        """序列化（**不包含 cookies**，避免审计报告泄露登录态）。"""
        return {
            "platform": self.platform,
            "test_url": self.test_url,
            "uid": self.uid,
            "share_url": self.share_url,
            "headers": self.headers,
            "field_semantics": self.field_semantics,
            "state_path": self.state_path,
            "state_fields": self.state_fields,
            "state_use_cookies": self.state_use_cookies,
            "html_use_cookies": self.html_use_cookies,
            "card_selector": self.card_selector,
            "dom_fields": {
                key: {"selector": value.selector, "attr": value.attr}
                for key, value in self.dom_fields.items()
            },
            "xhr_pattern": self.xhr_pattern,
            "xhr_fields": self.xhr_fields,
            "js_fields": self.js_fields,
            "max_scripts": self.max_scripts,
            "browser": {
                "headless": self.headless,
                "channel": self.channel,
                "data_dir": self.data_dir,
                "skip_version_check": self.skip_version_check,
                "wait_ms": self.browser_wait_ms,
                "xhr_wait_ms": self.xhr_wait_ms,
            },
            "timeout": self.timeout,
            "max_items": self.max_items,
            "login_required": self.login_required,
            "signature_required": self.signature_required,
            "score_overrides": self.score_overrides,
            "endpoints": [
                {
                    "name": endpoint.name,
                    "path": endpoint.path,
                    "url": endpoint.url,
                    "method": endpoint.method,
                    "params": endpoint.params,
                    "headers": endpoint.headers,
                    "signer": endpoint.signer,
                    "risk_codes": endpoint.risk_codes,
                    "required_fields": list(endpoint.required_fields),
                    "fields": endpoint.fields,
                    "field_semantics": endpoint.field_semantics,
                    "login_required": endpoint.login_required,
                    "signature_required": endpoint.signature_required,
                }
                for endpoint in self.endpoints
            ],
        }


def load_audit_config(path: str | Path) -> AuditConfig:
    """从 YAML 或 JSON 加载审计配置。YAML 需要 ``[config]`` extra。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                "读取 YAML 需要 PyYAML：pip install 'anti-crawl-kit[config]'"
            ) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是对象")
    return AuditConfig.from_dict(data)


# ── 运行期依赖（测试可替换）──────────────────────────


def _http_client(config: AuditConfig):
    from ..http import AntiRiskHttpClient

    return AntiRiskHttpClient(
        config.platform,
        timeout=config.timeout,
        max_retries=1,
        headers=config.headers or None,
    )


def _js_hunt(config: AuditConfig):
    from ..js_bundle import hunt_api

    return hunt_api(
        config.test_url,
        cookies=config.cookies or None,
        fields=config.js_fields,
        max_scripts=config.max_scripts,
        timeout=config.timeout,
    )


def _browser_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("patchright") is not None
    except Exception:
        return False


def _browser_session(config: AuditConfig):
    from ..session import BrowserSession

    return BrowserSession(
        headless=config.headless,
        data_dir=config.data_dir or None,
        channel=config.channel or None,
        anti_crawl=False,
        skip_version_check=config.skip_version_check,
    )


def _screenshot(page: Any, config: AuditConfig, name: str) -> str:
    if config.out_dir is None:
        return ""
    try:
        path = Path(config.out_dir) / "evidence" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path))
        return str(path)
    except Exception:
        return ""


def _notify_hard_stop(config: AuditConfig, result: PathResult) -> None:
    """验证墙 / 封禁：通知人工，然后由调用方硬停。"""
    try:
        from ..notify import Notifier

        Notifier().send(
            f"[{config.platform}] 路径审计硬停",
            f"{result.path} 触发 {result.stop_reason}：{result.evidence[:200]}",
        )
    except Exception:
        # 通知失败不能掩盖主流程的硬停
        pass


# ── 探测器：HTTP ─────────────────────────────────────


def _signed_view(endpoint: AuditEndpoint) -> SignedEndpoint:
    """转成 http_signed 的只读视图，复用参数渲染与返回码分类。"""
    return SignedEndpoint(
        name=endpoint.name,
        url=endpoint.url,
        method=endpoint.method,
        params=endpoint.params,
        headers=endpoint.headers,
        signer=endpoint.signer,
        risk_codes=endpoint.risk_codes,
        required_fields=endpoint.required_fields,
    )


def _request_endpoint(client: Any, endpoint: AuditEndpoint, config: AuditConfig):
    context = {"uid": config.uid, "platform": config.platform}
    params = _render_mapping(endpoint.params, context)
    headers = _render_mapping(endpoint.headers, context)
    cookies = config.cookies or None
    if endpoint.method == "POST":
        payload = _render_mapping(endpoint.json_data, context)
        return client.post_json(
            endpoint.url,
            json_data=payload,
            headers=headers,
            cookies=cookies,
        )
    return client.get_json(
        endpoint.url,
        params=params or None,
        headers=headers,
        cookies=cookies,
    )


def _probe_http_endpoints(
    config: AuditConfig,
    spec: PathSpec,
    *,
    browser_mode: str = "auto",
) -> PathResult:
    """匿名 / 登录 / 移动端 / GraphQL / 重放：统一按 endpoints 声明跑。"""
    result = _base_result(config, spec)
    endpoints = config.endpoints_for(spec.key)
    if not endpoints:
        return _not_tested(config, spec, spec.how_to_test)

    needs_login = spec.key == "login_http" or any(
        endpoint.login_required for endpoint in endpoints
    )
    if needs_login and not config.cookies:
        return _not_tested(
            config,
            spec,
            "未配置 cookies / cookies_env；登录态 HTTP 无法测（不是不可用）。",
        )

    client = _http_client(config)
    attempts: list[str] = []
    chosen: tuple[AuditEndpoint, Any, int, str, dict[str, Any]] | None = None
    started = time.perf_counter()

    for endpoint in endpoints:
        endpoint_started = time.perf_counter()
        try:
            response = _request_endpoint(client, endpoint, config)
        except Exception as exc:  # 单条接口异常不应中断整轮审计
            attempts.append(f"{endpoint.name}: exception {type(exc).__name__}: {exc}")
            continue
        latency = int((time.perf_counter() - endpoint_started) * 1000)

        risk = getattr(response, "risk", None)
        risk_kind = str(getattr(getattr(risk, "kind", ""), "value", "") or "")
        if risk_kind and risk_kind != "ok":
            reason = _RISK_TO_AUDIT.get(risk_kind, "unknown")
        else:
            signed_kind = classify_signed_response(
                int(getattr(response, "status_code", 0) or 0),
                getattr(response, "data", None),
                _signed_view(endpoint),
                text=str(getattr(response, "text", "") or ""),
            )
            reason = _SIGNED_TO_AUDIT.get(signed_kind, "unknown")

        data = getattr(response, "data", None)
        fields: dict[str, Any] = {}
        if isinstance(data, dict):
            for name, json_path in endpoint.fields.items():
                value = pick_path(data, json_path)
                if value is not None:
                    fields[name] = value
        if reason == "completed" and endpoint.fields and not fields:
            reason = "empty"

        attempts.append(
            f"{endpoint.name}: status={getattr(response, 'status_code', 0)} "
            f"reason={reason} {latency}ms"
        )
        current = (endpoint, response, latency, reason, fields)
        if chosen is None:
            chosen = current
        if reason == "completed":
            chosen = current
            break
        if reason in ("captcha", "blocked", "rate_limited"):
            chosen = current
            break

    total_latency = int((time.perf_counter() - started) * 1000)
    if chosen is None:
        result.stop_reason = "network"
        result.latency_ms = total_latency
        result.evidence = "; ".join(attempts) or "所有接口请求均异常"
        return result

    endpoint, response, latency, reason, fields = chosen
    result.status = int(getattr(response, "status_code", 0) or 0)
    result.latency_ms = latency
    result.stop_reason = reason
    result.fields = fields
    result.field_semantics = {
        **config.field_semantics,
        **endpoint.field_semantics,
    }
    result.login_required = bool(
        config.login_required or endpoint.login_required or needs_login
    )
    result.signature_required = bool(
        config.signature_required
        or endpoint.signature_required
        or endpoint.signer
    )
    snippet = _snippet(str(getattr(response, "text", "") or ""))
    result.evidence = "; ".join(attempts)
    if snippet:
        result.evidence += f"; body={snippet}"
    return result


def _probe_anonymous_json(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    return _probe_http_endpoints(config, spec, browser_mode=browser_mode)


def _probe_anonymous_graphql(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    return _probe_http_endpoints(config, spec, browser_mode=browser_mode)


def _probe_mobile_api(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    return _probe_http_endpoints(config, spec, browser_mode=browser_mode)


def _probe_login_http(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    return _probe_http_endpoints(config, spec, browser_mode=browser_mode)


def _probe_signature_replay(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    return _probe_http_endpoints(config, spec, browser_mode=browser_mode)


# ── 探测器：HTML / SSR ──────────────────────────────


def _probe_html_page(
    config: AuditConfig,
    spec: PathSpec,
    url: str,
    *,
    use_cookies: bool,
) -> PathResult:
    result = _base_result(config, spec)
    if not url:
        return _not_tested(config, spec, spec.how_to_test)

    client = _http_client(config)
    started = time.perf_counter()
    response = client.get_text(
        url,
        cookies=config.cookies if use_cookies and config.cookies else None,
        headers=config.headers or None,
    )
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    result.status = int(getattr(response, "status_code", 0) or 0)
    html = str(getattr(response, "text", "") or "")

    if result.status == 0:
        result.stop_reason = "network"
        result.evidence = f"request failed: {_snippet(html, 200)}"
        return result

    shape = classify_page_shape(html, status=result.status)
    empty = detect_empty_shell(html, selector=config.card_selector or "")
    reason = _SHAPE_TO_AUDIT.get(shape.kind, "unknown")
    if reason == "completed" and empty.is_empty:
        reason = "empty"
    if result.status in (401, 403) and reason == "completed":
        reason = "login_required"

    result.stop_reason = reason
    result.login_required = bool(
        config.login_required or reason == "login_required"
    )
    result.signature_required = bool(config.signature_required)
    result.evidence = (
        f"shape={shape.kind} bytes={len(html)} visible={shape.visible_chars} "
        f"states={shape.states} empty={empty.reasons}; "
        f"body={_snippet(html)}"
    )
    return result


def _probe_anonymous_html(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    return _probe_html_page(
        config, spec, config.test_url, use_cookies=config.html_use_cookies
    )


def _probe_share_ssr(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    return _probe_html_page(
        config, spec, config.share_url, use_cookies=config.html_use_cookies
    )


def _probe_ssr_state(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    result = _base_result(config, spec)
    if not config.test_url:
        return _not_tested(config, spec, spec.how_to_test)

    client = _http_client(config)
    started = time.perf_counter()
    response = client.get_text(
        config.test_url,
        cookies=config.cookies if config.state_use_cookies and config.cookies else None,
        headers=config.headers or None,
    )
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    result.status = int(getattr(response, "status_code", 0) or 0)
    html = str(getattr(response, "text", "") or "")

    if result.status == 0:
        result.stop_reason = "network"
        result.evidence = f"request failed: {_snippet(html, 200)}"
        return result

    state = extract_state(html)
    if not state.ok:
        result.stop_reason = "empty"
        result.evidence = (
            f"no usable state: key={state.key or '-'} error={state.error or '-'}; "
            f"body={_snippet(html)}"
        )
        return result

    fields: dict[str, Any] = {}
    for name, state_path in config.state_fields.items():
        value = pick_path(state.data, state_path)
        if value is not None:
            fields[name] = value

    extra = ""
    if config.state_path:
        items = pick_path(state.data, config.state_path)
        if isinstance(items, list):
            extra = f"; state_path={config.state_path} items={len(items)}"

    result.fields = fields
    result.field_semantics = dict(config.field_semantics)
    result.login_required = bool(config.login_required)
    result.signature_required = bool(config.signature_required)
    expected = set(config.field_semantics)
    if expected and not fields:
        result.stop_reason = "empty"
    else:
        result.stop_reason = "completed"
    keys = list(state.data)[:10] if isinstance(state.data, dict) else []
    result.evidence = (
        f"state={state.key} top_keys={keys} fields={list(fields)}{extra}"
    )
    return result


# ── 探测器：JS 包 ───────────────────────────────────


def _probe_js_bundle(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    result = _base_result(config, spec)
    if not config.test_url:
        return _not_tested(config, spec, spec.how_to_test)

    started = time.perf_counter()
    hunt = _js_hunt(config)
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    result.status = 200 if hunt.scanned_scripts else 0
    result.coverage = {
        "discovered": len(hunt.scripts),
        "scanned": hunt.scanned_scripts,
        "ratio": round(hunt.coverage, 4),
    }

    endpoint_samples = [item.resolved or item.value for item in hunt.endpoints[:8]]
    base_samples = [item.value for item in hunt.base_urls[:5]]
    field_samples = [item.value for item in hunt.field_hits[:8]]
    result.stop_reason = (
        "completed" if (hunt.endpoints or hunt.base_urls) else "empty"
    )
    result.evidence = (
        f"scripts={len(hunt.scripts)} scanned={hunt.scanned_scripts} "
        f"coverage={hunt.coverage:.0%}; endpoints={len(hunt.endpoints)} "
        f"base_urls={base_samples}; fields={field_samples}; "
        f"endpoint_samples={endpoint_samples}; warnings={hunt.warnings[:3]}"
    )
    return result


# ── 探测器：浏览器 ──────────────────────────────────


_DOM_EXTRACT_JS = """(payload) => {
  var out = {};
  Object.keys(payload).forEach(function(name) {
    var f = payload[name];
    var el = document.querySelector(f.selector);
    if (!el) { out[name] = null; return; }
    if (f.attr === 'text') out[name] = (el.innerText || el.textContent || '').trim();
    else if (f.attr === 'html') out[name] = el.innerHTML || '';
    else out[name] = el.getAttribute(f.attr) || '';
  });
  return out;
}"""


def _browser_guard(
    config: AuditConfig, spec: PathSpec, browser_mode: str
) -> PathResult | None:
    """返回 None 表示可以继续；否则返回 not_tested 结果。"""
    if browser_mode == "never":
        return _not_tested(config, spec, "本次以 --browser never 运行，浏览器路径未测。")
    if not _browser_available():
        return _not_tested(
            config,
            spec,
            "未安装 patchright（pip install 'anti-crawl-kit[browser]'），本条未测。",
        )
    return None


def _probe_dom(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    blocked = _browser_guard(config, spec, browser_mode)
    if blocked is not None:
        return blocked
    result = _base_result(config, spec)
    if not config.test_url or (not config.dom_fields and not config.card_selector):
        return _not_tested(config, spec, spec.how_to_test)

    started = time.perf_counter()
    with _browser_session(config) as page:
        page.goto(config.test_url, timeout=config.timeout * 1000)
        page.wait_for_timeout(config.browser_wait_ms)
        html = page.content()
        shape = classify_page_shape(html, status=200)
        reason = _SHAPE_TO_AUDIT.get(shape.kind, "unknown")

        card_count = 0
        if config.card_selector:
            card_count = int(
                page.evaluate(
                    "(sel) => document.querySelectorAll(sel).length",
                    config.card_selector,
                )
                or 0
            )

        fields: dict[str, Any] = {}
        if config.dom_fields:
            payload = {
                name: {"selector": item.selector, "attr": item.attr}
                for name, item in config.dom_fields.items()
            }
            values = page.evaluate(_DOM_EXTRACT_JS, payload) or {}
            fields = {
                name: value
                for name, value in values.items()
                if value not in (None, "")
            }

        if reason in HARD_STOP_REASONS:
            shot = _screenshot(page, config, spec.key)
            if shot:
                result.evidence = f"screenshot={shot}; "

    result.latency_ms = int((time.perf_counter() - started) * 1000)
    result.status = 200
    result.fields = fields
    result.field_semantics = dict(config.field_semantics)
    result.login_required = bool(
        config.login_required or reason == "login_required"
    )
    result.signature_required = bool(config.signature_required)
    if reason == "completed" and not fields and config.dom_fields:
        reason = "empty"
    result.stop_reason = reason
    result.evidence += (
        f"shape={shape.kind} cards={card_count} "
        f"fields={list(fields)} wait={config.browser_wait_ms}ms"
    )
    return result


def _probe_browser_xhr(config: AuditConfig, spec: PathSpec, *, browser_mode: str = "auto") -> PathResult:
    blocked = _browser_guard(config, spec, browser_mode)
    if blocked is not None:
        return blocked
    result = _base_result(config, spec)
    if not config.test_url or not config.xhr_pattern:
        return _not_tested(config, spec, spec.how_to_test)

    started = time.perf_counter()
    captured: list[dict[str, Any]] = []
    with _browser_session(config) as page:
        captured = page.capture_responses(config.xhr_pattern)
        page.goto(config.test_url, timeout=config.timeout * 1000)
        page.wait_for_timeout(config.xhr_wait_ms)
        page.stop_capture(config.xhr_pattern)
        html = page.content()
        shape = classify_page_shape(html, status=200)

        if shape.kind in ("captcha_wall", "blocked"):
            shot = _screenshot(page, config, spec.key)
            if shot:
                result.evidence = f"screenshot={shot}; "

    result.latency_ms = int((time.perf_counter() - started) * 1000)
    payload = captured[-1] if captured else None
    if payload is None:
        result.stop_reason = (
            "captcha"
            if shape.kind == "captcha_wall"
            else "blocked"
            if shape.kind == "blocked"
            else "empty"
        )
        result.evidence += (
            f"captured=0 pattern={config.xhr_pattern} shape={shape.kind}; "
            "可能未登录、接口改名或未触发。"
        )
        return result

    fields: dict[str, Any] = {}
    for name, json_path in config.xhr_fields.items():
        value = pick_path(payload, json_path)
        if value is not None:
            fields[name] = value

    result.status = 200
    result.fields = fields
    result.field_semantics = dict(config.field_semantics)
    result.login_required = bool(config.login_required)
    result.signature_required = bool(config.signature_required)
    result.stop_reason = "completed"
    if config.xhr_fields and not fields:
        result.stop_reason = "empty"
    keys = list(payload)[:10] if isinstance(payload, dict) else []
    result.evidence += (
        f"captured={len(captured)} pattern={config.xhr_pattern} "
        f"top_keys={keys} fields={list(fields)} shape={shape.kind}"
    )
    return result


PROBES: dict[str, Callable[..., PathResult]] = {
    "anonymous_html": _probe_anonymous_html,
    "anonymous_json": _probe_anonymous_json,
    "anonymous_graphql": _probe_anonymous_graphql,
    "mobile_api": _probe_mobile_api,
    "share_ssr": _probe_share_ssr,
    "ssr_state": _probe_ssr_state,
    "js_bundle": _probe_js_bundle,
    "login_http": _probe_login_http,
    "browser_xhr": _probe_browser_xhr,
    "signature_replay": _probe_signature_replay,
    "dom": _probe_dom,
}


# ── 评分卡 ──────────────────────────────────────────


def _score_cost(result: PathResult) -> int:
    if result.path == "ocr":
        return 1
    latency = result.latency_ms
    if latency <= 0:
        return 3
    if latency < 500:
        return 5
    if latency < 1500:
        return 4
    if latency < 5000:
        return 3
    if latency < 10000:
        return 2
    return 1


def _score_dependency(result: PathResult) -> int:
    if not result.login_required and not result.signature_required:
        return 5
    if result.login_required and result.signature_required:
        return 1
    return 3


def _score_stability(result: PathResult) -> int:
    if result.path in {
        "anonymous_html",
        "anonymous_json",
        "anonymous_graphql",
        "ssr_state",
        "share_ssr",
    }:
        return 5
    if result.path == "mobile_api":
        return 4 if not result.login_required else 3
    if result.path in {"login_http", "open_platform", "app_api", "signature_replay"}:
        return 3
    if result.path == "browser_xhr":
        return 2
    return 1  # dom / ocr


def _score_risk(result: PathResult) -> int:
    if not result.login_required and not result.signature_required:
        return 5
    if result.login_required and not result.signature_required:
        return 3
    if not result.login_required and result.signature_required:
        return 2
    return 1


def _score_maintenance(result: PathResult) -> int:
    mapping = {
        "anonymous_html": 4,
        "anonymous_json": 5,
        "anonymous_graphql": 4,
        "mobile_api": 3,
        "share_ssr": 4,
        "ssr_state": 4,
        "js_bundle": 3,
        "login_http": 4,
        "browser_xhr": 3,
        "signature_replay": 2,
        "open_platform": 5,
        "app_api": 2,
        "dom": 2,
        "ocr": 1,
    }
    return mapping.get(result.path, 3)


def score_path(
    result: PathResult,
    *,
    overrides: dict[str, int] | None = None,
) -> PathResult:
    """按 docs/23 的 5 维评分卡打分；硬门槛不过直接标记 ``rejected``。

    口径正确是 veto：没有字段口径、没取到字段、路径没成功，
    都不进入候选，速度再快也没用。
    """
    result.scores = {}
    result.total_score = 0
    result.rejected = ""

    spec = PATHS.get(result.path)
    if spec is not None and spec.role == "discovery":
        result.rejected = "发现路径，不直接产出字段（只用于找接口）"
        return result
    if not result.tested:
        result.rejected = "未测路径，不能下结论"
        return result
    if result.stop_reason != "completed":
        result.rejected = f"路径未成功（{result.stop_reason}）"
        return result
    if not result.fields:
        result.rejected = "未取到任何字段"
        return result
    if not result.field_semantics:
        result.rejected = "未声明字段口径（口径是 veto）"
        return result
    missing = [name for name in result.fields if name not in result.field_semantics]
    if missing:
        result.rejected = f"字段缺口径: {', '.join(missing)}"
        return result

    scores = {
        "cost": _score_cost(result),
        "dependency": _score_dependency(result),
        "stability": _score_stability(result),
        "risk": _score_risk(result),
        "maintenance": _score_maintenance(result),
    }
    for dimension, value in (overrides or {}).items():
        if dimension in scores:
            scores[dimension] = max(1, min(5, int(value)))
    result.scores = scores
    result.total_score = sum(scores.values())
    return result


# ── 选型建议 ────────────────────────────────────────


@dataclass
class Recommendation:
    """生产选型卡（docs/23 第 3.3 节）。"""

    platform: str
    primary: dict[str, Any] | None = None
    fallback: dict[str, Any] | None = None
    rollback: dict[str, Any] = field(default_factory=dict)
    rejected: list[dict[str, str]] = field(default_factory=list)
    last_verified: str = ""
    production_ready: bool = False
    acceptance_gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "primary": self.primary,
            "fallback": self.fallback,
            "rollback": self.rollback,
            "rejected": self.rejected,
            "last_verified": self.last_verified,
            "production_ready": self.production_ready,
            "acceptance_gaps": self.acceptance_gaps,
        }


def _candidate(result: PathResult, *, role: str, allowed: bool = True) -> dict[str, Any]:
    if role == "primary":
        reason = f"评分 {result.total_score}/25；{result.label}"
    else:
        reason = f"评分 {result.total_score}/25；兜底路径"
    item: dict[str, Any] = {
        "path": result.path,
        "layer": result.layer,
        "reason": reason,
        "latency_ms": result.latency_ms,
        "success_rate": "待补：30 目标 × 3 轮",
        "login_required": result.login_required,
        "signature_required": result.signature_required,
        "allowed_for_product": allowed,
    }
    return item


def recommend(platform: str, results: list[PathResult]) -> Recommendation:
    """按评分卡选出 primary / fallback / rollback。

    重要：**单次审计不等于生产验收**。即使选出 primary，
    ``production_ready`` 仍为 False，直到补齐成功率与 3 天稳定性。
    """
    tested = [result for result in results if result.tested]
    eligible = sorted(
        [result for result in tested if not result.rejected],
        key=lambda item: (-item.total_score, item.latency_ms),
    )

    recommendation = Recommendation(platform=platform)
    recommendation.last_verified = max(
        (result.tested_at for result in tested if result.tested_at), default=""
    )
    recommendation.rejected = [
        {"path": result.path, "reason": result.rejected}
        for result in tested
        if result.rejected
    ]

    if eligible:
        primary = eligible[0]
        recommendation.primary = _candidate(primary, role="primary")
        if len(eligible) > 1:
            recommendation.fallback = _candidate(eligible[1], role="fallback")
        else:
            # 没有第二条合格路径时，允许把「能跑但口径/门槛不过」的路径
            # 列为对照项，但明确 allowed_for_product = False。
            vetoed = [
                result
                for result in tested
                if result.rejected
                and result.stop_reason == "completed"
                and PATHS.get(result.path) is not None
                and PATHS[result.path].role == "data"
            ]
            if vetoed:
                vetoed.sort(key=lambda item: item.latency_ms)
                item = _candidate(vetoed[0], role="fallback", allowed=False)
                item["reason"] = f"未通过硬门槛：{vetoed[0].rejected}；只作对照"
                recommendation.fallback = item

        values = [primary.path]
        if recommendation.fallback:
            values.append(str(recommendation.fallback["path"]))
        recommendation.rollback = {
            "env": "SOURCE_MODE",
            "values": list(dict.fromkeys(values)),
        }
        recommendation.acceptance_gaps = [
            "主路径成功率需 30 目标 × 3 轮 ≥95%（当前是单次探测）",
            "需连续 3 天同一时段复现，排除「一次正常一次验证页」",
            "需在生产配置中落地失效检测点与频率上限",
        ]
    else:
        recommendation.acceptance_gaps = [
            "没有通过硬门槛的路径；不要写「生产可用」",
            "未测路径需先补探测，不能写成「不可用」",
        ]

    recommendation.production_ready = False
    return recommendation


# ── 运行与输出 ──────────────────────────────────────


@dataclass
class AuditRun:
    """一次完整审计的结果集合。"""

    platform: str
    results: list[PathResult]
    recommendation: Recommendation
    started_at: str = ""
    finished_at: str = ""
    hard_stop: str = ""

    def report(self) -> RunReport:
        outcomes = [
            {
                "path": result.path,
                "ok": result.stop_reason == "completed",
                "stop_reason": result.stop_reason,
                "latency_ms": result.latency_ms,
                "status": result.status,
                "fields": list(result.fields),
                "rejected": result.rejected,
            }
            for result in self.results
            if result.tested
        ]
        coverage: dict[str, Any] = {}
        for result in self.results:
            if result.coverage:
                coverage = dict(result.coverage)
                break
        report = RunReport.from_outcomes(
            "path_audit",
            self.platform,
            outcomes,
            mode="audit",
            coverage=coverage,
            stop_reason=self.hard_stop or None,
            meta={
                "recommendation": self.recommendation.to_dict(),
                "hard_stop": self.hard_stop,
                "path_matrix": [result.to_dict() for result in self.results],
            },
        )
        report.started_at = self.started_at or report.started_at
        report.finished_at = self.finished_at or report.finished_at
        return report

    def write(self, out_dir: str | Path) -> tuple[Path, Path]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        report_path = self.report().write(out / "run_report.json")
        markdown_path = out / "audit.md"
        markdown_path.write_text(render_markdown(self), encoding="utf-8")
        return report_path, markdown_path

    def exit_code(self) -> int:
        return 2 if self.hard_stop else 0


def run_audit(
    config: AuditConfig,
    *,
    only: list[str] | None = None,
    browser_mode: str = "auto",
    out_dir: str | Path | None = None,
) -> AuditRun:
    """按路径目录逐条探测，遇到验证墙/封禁硬停。"""
    if only:
        unknown = [key for key in only if key not in PATHS]
        if unknown:
            raise ValueError(
                f"未知路径: {unknown}；可用: {[spec.key for spec in PATH_CATALOG]}"
            )

    config.out_dir = Path(out_dir) if out_dir else None
    selected = [
        spec for spec in PATH_CATALOG if only is None or spec.key in only
    ]
    selected_keys = {spec.key for spec in selected}
    results: list[PathResult] = []
    hard_stop = ""
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")

    for spec in selected:
        if hard_stop:
            result = _not_tested(
                config,
                spec,
                f"前置路径触发 {hard_stop}，按红线硬停；本条未测。",
            )
            results.append(result)
            continue

        probe = PROBES.get(spec.key)
        if probe is None:
            result = _not_tested(config, spec, spec.how_to_test)
        else:
            try:
                result = probe(config, spec, browser_mode=browser_mode)
            except Exception as exc:  # 单条路径异常不掩盖其他路径
                result = _base_result(config, spec)
                result.stop_reason = "network"
                result.evidence = f"{type(exc).__name__}: {exc}"
        result.tested = result.stop_reason != "not_tested"
        if result.stop_reason in HARD_STOP_REASONS:
            hard_stop = result.stop_reason
            _notify_hard_stop(config, result)
        results.append(result)

    # 未选中的路径也要出现在矩阵里，标 not_tested
    for spec in PATH_CATALOG:
        if spec.key in selected_keys:
            continue
        results.append(
            _not_tested(config, spec, "本次 --only 未选中，未测。")
        )
    results.sort(key=lambda item: PATHS[item.path].index)

    for result in results:
        score_path(result, overrides=config.score_overrides.get(result.path))

    recommendation = recommend(config.platform, results)
    return AuditRun(
        platform=config.platform,
        results=results,
        recommendation=recommendation,
        started_at=started_at,
        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        hard_stop=hard_stop,
    )


# ── Markdown 报告 ───────────────────────────────────


def _status_label(result: PathResult) -> str:
    if result.stop_reason == "completed":
        spec = PATHS.get(result.path)
        if spec is not None and spec.role == "discovery":
            return "✅ 发现接口"
        return "✅ 通"
    mapping = {
        "not_tested": "⏳ 未测",
        "login_required": "🔒 需登录",
        "captcha": "❌ 验证码",
        "blocked": "❌ 被封",
        "rate_limited": "❌ 限频",
        "signature_failed": "⚠️ 签名失败",
        "parameter_error": "⚠️ 参数错",
        "network": "❌ 网络",
        "not_found": "❌ 不存在",
        "empty": "⚠️ 空结果",
    }
    return mapping.get(result.stop_reason, result.stop_reason)


def _md_cell(value: str, limit: int = 120) -> str:
    text = " ".join((value or "").split()).replace("|", "\\|")
    return text[:limit]


def render_markdown(run: AuditRun) -> str:
    """输出可读的审计矩阵 + 评分卡 + 选型卡。"""
    lines: list[str] = [
        f"# {run.platform} · 平台路径全量审计",
        "",
        f"- 开始：{run.started_at}",
        f"- 结束：{run.finished_at}",
        f"- 硬停：{run.hard_stop or '无'}",
        "",
        "> 未测路径写「未测」，不写「不可用」。",
        "> 单次审计只做初筛，**不构成生产验收**。",
        "",
        "## 1. 路径矩阵",
        "",
        "| # | 路径 | 层级 | 状态 | 耗时 | 登录 | 签名 | 字段 | 证据 / 说明 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for result in run.results:
        fields = ", ".join(f"{key}={value}" for key, value in result.fields.items())
        detail = result.evidence or result.note
        if result.rejected and result.stop_reason != "not_tested":
            detail = f"{detail}；淘汰：{result.rejected}"
        lines.append(
            "| {index} | {label} | {layer} | {status} | {latency} | {login} | {sign} | {fields} | {detail} |".format(
                index=PATHS[result.path].index,
                label=result.label,
                layer=result.layer,
                status=_status_label(result),
                latency=f"{result.latency_ms}ms" if result.tested else "-",
                login="是" if result.login_required else "否",
                sign="是" if result.signature_required else "否",
                fields=_md_cell(fields, 60) or "-",
                detail=_md_cell(detail, 180),
            )
        )

    lines += [
        "",
        "## 2. 评分卡（仅通过硬门槛的路径）",
        "",
        "| 路径 | 成本 | 依赖 | 稳定性 | 风控 | 维护 | 总分 |",
        "|---|---|---|---|---|---|---|",
    ]
    scored = [result for result in run.results if result.total_score > 0]
    if scored:
        for result in sorted(scored, key=lambda item: -item.total_score):
            scores = result.scores
            lines.append(
                f"| {result.path} | {scores.get('cost', '-')} | "
                f"{scores.get('dependency', '-')} | {scores.get('stability', '-')} | "
                f"{scores.get('risk', '-')} | {scores.get('maintenance', '-')} | "
                f"**{result.total_score}/25** |"
            )
    else:
        lines.append("| - | - | - | - | - | - | 无 |")

    lines += [
        "",
        "## 3. 生产选型卡",
        "",
        "```yaml",
        json.dumps(run.recommendation.to_dict(), ensure_ascii=False, indent=2),
        "```",
        "",
    ]

    untested = [
        result for result in run.results if result.stop_reason == "not_tested"
    ]
    if untested:
        lines += ["## 4. 未测路径怎么测", ""]
        for result in untested:
            spec = PATHS[result.path]
            note = result.note or ""
            if spec.how_to_test and spec.how_to_test not in note:
                note = f"{note}；测法：{spec.how_to_test}" if note else spec.how_to_test
            lines.append(f"- **{result.path}**（{result.layer}）：{note}")
        lines.append("")

    lines += [
        "## 5. 验收清单",
        "",
        "- [ ] 14 条路径逐条有结论（未测的写「未测」）",
        "- [ ] 每条路径有 status / latency / 登录 / 签名 / 字段口径 / 证据",
        "- [ ] 至少两条路径做过双跑校验（同目标、同时间窗）",
        "- [ ] 口径不同的源已标注，未混用",
        "- [ ] 主路径成功率 ≥95%（30 目标 × 3 轮或等价样本）",
        "- [ ] 连续 3 天可复现，不是单次侥幸",
        "- [ ] 有兜底路径和 SOURCE_MODE 回滚开关",
        "- [ ] 有失效检测点、频率上限和停止条件",
        "",
    ]
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────


def _print_summary(run: AuditRun) -> None:
    print(f"[audit] platform={run.platform}")
    for result in run.results:
        if result.stop_reason == "not_tested":
            print(f"  [--] {result.path:<18} {result.layer:<5} 未测：{result.note}")
            continue
        print(
            f"  [{'OK' if result.stop_reason == 'completed' else '!!'}] "
            f"{result.path:<18} {result.layer:<5} {result.status} "
            f"{result.latency_ms}ms fields={list(result.fields)} "
            f"reason={result.stop_reason}"
        )
    recommendation = run.recommendation
    if recommendation.primary:
        print(
            f"[rec] primary={recommendation.primary['path']} "
            f"score={recommendation.primary['reason']}"
        )
    else:
        print("[rec] 没有通过硬门槛的路径（未测路径不代表不可用）")
    if recommendation.fallback:
        print(f"[rec] fallback={recommendation.fallback['path']}")
    if run.hard_stop:
        print(f"[HARD STOP] {run.hard_stop}：已按红线停止，不再尝试其他路径")
    print("[warn] 单次审计不构成生产验收；成功率与 3 天稳定性需另外补实验。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="平台路径全量审计（docs/23 的 14 条路径矩阵）"
    )
    parser.add_argument("--config", required=True, help="平台审计配置（YAML / JSON）")
    parser.add_argument(
        "--only",
        default="",
        help="只跑指定路径，逗号分隔；例如 anonymous_json,ssr_state",
    )
    parser.add_argument(
        "--browser",
        choices=("auto", "always", "never"),
        default="auto",
        help="浏览器路径策略：auto 装了 patchright 才跑（默认）",
    )
    parser.add_argument("--headed", action="store_true", help="有头浏览器")
    parser.add_argument(
        "--skip-version-check",
        action="store_true",
        help="跳过 patchright / Chrome 大版本检查（只用于已实测环境）",
    )
    parser.add_argument("--out", default="", help="输出目录")
    parser.add_argument("--max-items", type=int, default=0, help="覆盖配置里的 max_items")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    try:
        config = load_audit_config(args.config)
    except Exception as exc:
        print(f"[FAIL] 读取配置失败: {type(exc).__name__}: {exc}")
        return 1

    if args.headed:
        config.headless = False
    if args.skip_version_check:
        config.skip_version_check = True
    if args.max_items > 0:
        config.max_items = args.max_items

    only = [item.strip() for item in args.only.split(",") if item.strip()] or None
    out_dir = Path(args.out) if args.out else Path("runs") / (
        f"{config.platform}-{date.today().isoformat()}"
    )

    try:
        run = run_audit(
            config,
            only=only,
            browser_mode=args.browser,
            out_dir=out_dir,
        )
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 1

    report_path, markdown_path = run.write(out_dir)
    _print_summary(run)
    print(f"[out] {report_path}")
    print(f"[out] {markdown_path}")
    return run.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
