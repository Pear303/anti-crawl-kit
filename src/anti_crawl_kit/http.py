"""HTTP 直连反爬客户端 —— 用 curl_cffi 伪装浏览器 TLS 指纹。

适用场景（优先级：HTTP 直连 > 浏览器）：
    - 公开 JSON 接口（榜单、搜索、详情）
    - 已拿到有效 cookie / 签名参数的接口
    - 批量拉取，浏览器成本太高

不适用：
    - 需要执行 JS 生成签名（a_bogus / w_rid）
    - 强风控接口（直接 403 / 429）

用法::

    client = AntiRiskHttpClient("bilibili")
    r = client.get_json("https://api.bilibili.com/x/web-interface/popular")
    if r.risk.should_fallback_to_browser:
        ...  # 切浏览器
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any

from curl_cffi import requests

from .risk import RiskDecision, RiskKind, classify_http_result
from .user_agent import get_ua_for_impersonate

# UA 必须与默认 impersonate（chrome120）的 TLS 指纹同版本
DEFAULT_UA = get_ua_for_impersonate("chrome120")


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    text: str
    data: Any
    headers: dict[str, str]
    url: str
    risk: RiskDecision


class AntiRiskHttpClient:
    def __init__(
        self,
        platform: str,
        *,
        impersonate: str = "chrome120",
        timeout: int = 20,
        max_retries: int = 3,
        proxy: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.platform = platform
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_retries = max_retries
        self.proxy = proxy
        self.headers = {**self._default_headers(), **(headers or {})}
        self._session = requests.Session()

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> HttpResult:
        result = self.get_text(url, params=params, headers=headers, cookies=cookies)
        data = None
        if result.text:
            try:
                data = json.loads(result.text)
            except json.JSONDecodeError:
                return HttpResult(
                    result.status_code,
                    result.text,
                    None,
                    result.headers,
                    result.url,
                    RiskDecision(RiskKind.NETWORK_ERROR, "invalid_json", True),
                )
        return HttpResult(
            result.status_code, result.text, data, result.headers, result.url, result.risk
        )

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> HttpResult:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                merged_headers = {**self.headers, **(headers or {})}
                kwargs: dict[str, Any] = {
                    "params": params,
                    "headers": merged_headers,
                    "timeout": self.timeout,
                    "impersonate": self.impersonate,
                }
                if self.proxy:
                    kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
                if cookies:
                    kwargs["cookies"] = cookies
                response = self._session.get(url, **kwargs)
                risk = classify_http_result(
                    response.status_code, response.text, getattr(response, "url", url)
                )
                return HttpResult(
                    status_code=response.status_code,
                    text=response.text,
                    data=None,
                    headers=dict(getattr(response, "headers", {}) or {}),
                    url=str(getattr(response, "url", url)),
                    risk=risk,
                )
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 10) + random.uniform(0, 0.5))
        return HttpResult(
            0,
            str(last_error or "network error"),
            None,
            {},
            url,
            RiskDecision(RiskKind.NETWORK_ERROR, "request_exception", True),
        )

    def post_json(
        self,
        url: str,
        *,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> HttpResult:
        result = self.post_text(
            url, json_data=json_data, headers=headers, cookies=cookies
        )
        data = None
        if result.text:
            try:
                data = json.loads(result.text)
            except json.JSONDecodeError:
                return HttpResult(
                    result.status_code,
                    result.text,
                    None,
                    result.headers,
                    result.url,
                    RiskDecision(RiskKind.NETWORK_ERROR, "invalid_json", True),
                )
        return HttpResult(
            result.status_code, result.text, data, result.headers, result.url, result.risk
        )

    def post_text(
        self,
        url: str,
        *,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> HttpResult:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                merged_headers = {**self.headers, **(headers or {})}
                kwargs: dict[str, Any] = {
                    "headers": merged_headers,
                    "timeout": self.timeout,
                    "impersonate": self.impersonate,
                }
                if json_data:
                    kwargs["json"] = json_data
                if self.proxy:
                    kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
                if cookies:
                    kwargs["cookies"] = cookies
                response = self._session.post(url, **kwargs)
                risk = classify_http_result(
                    response.status_code, response.text, getattr(response, "url", url)
                )
                return HttpResult(
                    status_code=response.status_code,
                    text=response.text,
                    data=None,
                    headers=dict(getattr(response, "headers", {}) or {}),
                    url=str(getattr(response, "url", url)),
                    risk=risk,
                )
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 10) + random.uniform(0, 0.5))
        return HttpResult(
            0,
            str(last_error or "network error"),
            None,
            {},
            url,
            RiskDecision(RiskKind.NETWORK_ERROR, "request_exception", True),
        )

    def _default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
        }
