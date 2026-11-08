"""平台级 HTTP Headers 工厂 + Referer 推断。

为什么 Referer 很重要：
    视频/音频直链 CDN 普遍有防盗链校验，不带 Referer 会 403 或下载到空文件。
"""
from __future__ import annotations

from urllib.parse import urlparse

from .user_agent import get_random_user_agent

_PLATFORM_REFERER: dict[str, str] = {
    "bilibili": "https://www.bilibili.com/",
    "douyin": "https://www.douyin.com/",
    "kuaishou": "https://www.kuaishou.com/",
    "zhihu": "https://www.zhihu.com/hot",
    "weibo": "https://weibo.com/",
    "toutiao": "https://www.toutiao.com/",
    "csdn": "https://blog.csdn.net/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "v2ex": "https://www.v2ex.com/",
    "xueqiu": "https://xueqiu.com/",
    "baidu": "https://www.baidu.com/",
}


def get_platform_headers(
    platform: str,
    *,
    accept: str = "json",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """获取平台级 HTTP headers。

    Args:
        platform: 平台标识（bilibili/douyin/...），未识别则用通用头。
        accept: "json" 或 "html"。
        extra: 额外 headers（可覆盖默认值）。
    """
    headers: dict[str, str] = {
        "User-Agent": get_random_user_agent(),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if accept == "json":
        headers["Accept"] = "application/json, text/plain, */*"
    else:
        headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        )
    referer = _PLATFORM_REFERER.get(platform)
    if referer:
        headers["Referer"] = referer
    if extra:
        headers.update(extra)
    return headers


# CDN 域名关键词 -> Referer 映射
_CDN_HOST_KEYWORDS: list[tuple[list[str], str]] = [
    (["bilivideo", "biliapi", "hdslb", "bilibili", "mountaintoys"], "https://www.bilibili.com/"),
    (["kuaishou", "yximgs", "ksapisrv", "kwai"], "https://www.kuaishou.com/"),
    (["douyin", "amemv", "byteicdn", "bytedance"], "https://www.douyin.com/"),
]


def get_referer_for_url(url: str) -> str | None:
    """根据 URL 的 hostname 推断合适的 Referer。"""
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    if not host:
        return None
    for keywords, referer in _CDN_HOST_KEYWORDS:
        if any(kw in host for kw in keywords):
            return referer
    return None
