"""带签名的 HTTP 数据源规格（L1 登录态 / 签名接口）。

设计原则
--------
工作包**不内置任何签名算法**（WBI、a_bogus、x-zse-96 等）。原因：

1. 算法会变，内置实现三个月就过期；
2. 逆算法有法律与维护风险；
3. 真实浏览器已经能生成签名，没必要复刻。

所以这里只定义**契约**：

- ``HttpSignedSpec`` 描述「有哪些接口、参数怎么填、风控码怎么分类」；
- ``register_signer`` 让使用者注入自己的签名函数（或从浏览器导出的签名）；
- ``classify_signed_response`` 把返回码翻译成统一的失败分类。

YAML 示例见 ``examples/platforms/bilibili_http_signed.yaml``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# 统一的失败分类，和 datasource.StopReason 对齐
SIGNED_RISK_KINDS = (
    "ok",
    "login_required",
    "captcha",
    "rate_limited",
    "signature_failed",
    "param_error",
    "not_found",
    "empty",
    "network",
)


@dataclass(frozen=True)
class SignedEndpoint:
    """单个接口的声明。"""

    name: str
    url: str
    method: str = "GET"
    params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    # 签名器名称；空字符串表示该接口不需要签名
    signer: str = ""
    # 返回码 → 失败分类，例如 {"-352": "captcha", "-799": "rate_limited"}
    risk_codes: dict[str, str] = field(default_factory=dict)
    # 必须存在的字段路径，用于判定「返回 200 但其实是空壳」
    required_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class HttpSignedSpec:
    """一个平台的带签名数据源描述。"""

    name: str
    platform: str
    endpoints: dict[str, SignedEndpoint] = field(default_factory=dict)
    # 登录态检查：``cookie:SESSDATA`` 或一段浏览器 JS 表达式
    auth_check: str = ""
    # 路径优先级：先试哪个，失败后回退到哪个
    fallback: tuple[str, ...] = ("http_signed", "browser_xhr")
    mode: str = "http_signed"

    def endpoint(self, name: str) -> SignedEndpoint:
        if name not in self.endpoints:
            raise KeyError(f"{self.platform} 没有接口 {name!r}，可用: {list(self.endpoints)}")
        return self.endpoints[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "platform": self.platform,
            "mode": self.mode,
            "auth_check": self.auth_check,
            "fallback": list(self.fallback),
            "endpoints": {
                key: {
                    "url": ep.url,
                    "method": ep.method,
                    "params": ep.params,
                    "headers": ep.headers,
                    "signer": ep.signer,
                    "risk_codes": ep.risk_codes,
                    "required_fields": list(ep.required_fields),
                }
                for key, ep in self.endpoints.items()
            },
        }


# ── 签名器注册表 ─────────────────────────────────────
Signer = Callable[[dict[str, Any]], dict[str, Any]]
_SIGNERS: dict[str, Signer] = {}


def register_signer(name: str) -> Callable[[Signer], Signer]:
    """注册一个签名函数。

    签名函数接收请求上下文（url / params / headers / cookies），
    返回需要追加的 params 或 headers。例如::

        @register_signer("bilibili_wbi")
        def wbi(ctx):
            return {"params": {"w_rid": "...", "wts": "..."}}
    """

    def decorator(fn: Signer) -> Signer:
        _SIGNERS[name] = fn
        return fn

    return decorator


def get_signer(name: str) -> Signer:
    if name not in _SIGNERS:
        raise KeyError(
            f"未注册签名器 {name!r}。工作包不内置签名算法；"
            "请用 register_signer 注入，或改走浏览器 XHR 路径。"
        )
    return _SIGNERS[name]


def list_signers() -> list[str]:
    return sorted(_SIGNERS)


# ── 规格加载 ─────────────────────────────────────────
def _endpoint_from_dict(name: str, data: dict[str, Any]) -> SignedEndpoint:
    return SignedEndpoint(
        name=name,
        url=data["url"],
        method=str(data.get("method", "GET")).upper(),
        params={str(k): str(v) for k, v in (data.get("params") or {}).items()},
        headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
        signer=str(data.get("signer", "")),
        risk_codes={str(k): str(v) for k, v in (data.get("risk_codes") or {}).items()},
        required_fields=tuple(data.get("required_fields") or ()),
    )


def spec_from_dict(data: dict[str, Any]) -> HttpSignedSpec:
    endpoints = {
        name: _endpoint_from_dict(name, ep)
        for name, ep in (data.get("endpoints") or {}).items()
    }
    return HttpSignedSpec(
        name=data.get("name", ""),
        platform=data.get("platform", ""),
        endpoints=endpoints,
        auth_check=str(data.get("auth_check", "")),
        fallback=tuple(data.get("fallback") or ("http_signed", "browser_xhr")),
        mode=str(data.get("mode", "http_signed")),
    )


def load_http_signed_spec(path: str | Path) -> HttpSignedSpec:
    """从 YAML 或 JSON 加载规格。YAML 需要 ``[config]`` extra。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("读取 YAML 需要 PyYAML：pip install 'anti-crawl-kit[config]'") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是对象")
    return spec_from_dict(data)


# ── 返回码分类 ───────────────────────────────────────
def _pick(data: Any, path: str) -> Any:
    """按 ``a.b.c`` 取嵌套字段，取不到返回 None。"""
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def classify_signed_response(
    status: int,
    data: Any,
    endpoint: SignedEndpoint,
    *,
    text: str = "",
) -> str:
    """把一次接口返回翻译成统一分类。

    判定顺序：HTTP 状态 → 平台返回码 → 必填字段 → 空响应。
    """
    if status == 0:
        return "network"
    if status in (401, 403):
        return "login_required"
    if status == 412:
        return "captcha"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "network"

    if isinstance(data, dict):
        code = data.get("code", data.get("result"))
        if code is not None:
            kind = endpoint.risk_codes.get(str(code))
            if kind:
                return kind

        for path in endpoint.required_fields:
            if _pick(data, path) is None:
                return "empty"

    if not data and not text.strip():
        return "empty"
    return "ok"


def render_params(
    endpoint: SignedEndpoint,
    context: dict[str, str],
) -> dict[str, str]:
    """把 ``{uid}`` 这类占位符替换成实际值。"""
    rendered: dict[str, str] = {}
    for key, value in endpoint.params.items():
        try:
            rendered[key] = value.format(**context)
        except (KeyError, IndexError):
            rendered[key] = value
    return rendered
