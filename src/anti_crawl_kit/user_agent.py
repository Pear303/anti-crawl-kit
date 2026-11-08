"""User-Agent 统一入口。

★ 最重要的规则：
    浏览器会话默认 **不要覆写 UA**，跟随真实浏览器。
    固定 UA 会与真实 Chrome 版本、sec-ch-ua 头产生矛盾，反而是检测信号。

    HTTP 直连（requests / curl_cffi）才需要 UA，
    而且 **UA 的版本必须与 impersonate 的 TLS 指纹版本一致**。
    用 Chrome 120 的 TLS 指纹 + Chrome 131 的 UA，是自相矛盾的。
"""
from __future__ import annotations

import random as _random

# impersonate 名称 → 对应 UA。
# curl_cffi 的 impersonate 决定 TLS/JA3/HTTP2 指纹，UA 必须同版本。
_UA_BY_IMPERSONATE: dict[str, str] = {
    "chrome110": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/110.0.0.0 Safari/537.36"
    ),
    "chrome116": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0.0.0 Safari/537.36"
    ),
    "chrome120": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "chrome124": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "chrome131": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}

_DEFAULT_IMPERSONATE = "chrome120"

CHROME_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)


def get_ua_for_impersonate(impersonate: str = _DEFAULT_IMPERSONATE) -> str:
    """返回与 impersonate 版本匹配的 UA。

    未知版本回退到 chrome120，避免 UA 与 TLS 指纹跨版本。
    """
    key = (impersonate or "").strip().lower()
    return _UA_BY_IMPERSONATE.get(key, _UA_BY_IMPERSONATE[_DEFAULT_IMPERSONATE])


def get_user_agent(
    platform: str | None = None,
    *,
    mobile: bool = False,
    randomize: bool = False,
) -> str:
    """获取固定 UA（HTTP 直连用）。

    浏览器场景不要调用这个函数——让系统 Chrome 用默认 UA。
    """
    if mobile:
        return CHROME_MOBILE_UA
    return _UA_BY_IMPERSONATE[_DEFAULT_IMPERSONATE]


def get_random_user_agent(*, mobile: bool = False) -> str:
    """返回随机桌面/移动端 UA（仅 HTTP 直连用）。

    只在同代版本里随机，不跨大版本，避免与 TLS 指纹矛盾。
    """
    if mobile:
        return CHROME_MOBILE_UA
    return _random.choice(
        [
            _UA_BY_IMPERSONATE["chrome120"],
            _UA_BY_IMPERSONATE["chrome124"],
            _UA_BY_IMPERSONATE["chrome131"],
        ]
    )
