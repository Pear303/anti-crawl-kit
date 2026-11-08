"""资源拦截器（不推荐默认使用）—— 屏蔽图片/字体/媒体。

⚠ 结论（2026-11 审计）：
    默认不要开。实测提速有限，而且最容易把验证码/二维码一起拦掉。

    - `domcontentloaded` 通常在图片加载前就触发，拦截对首屏帮助不大；
    - 会把验证码图片、登录二维码 abort，用户永远等不到验证通过；
    - 工作包里 3 个登录/加载示例都显式传 `block_resources=False`。

    只在「纯文本列表页、已登录、确认无验证码」的场景临时开启，
    并且要配 `whitelist_domains` 放行二维码域名。

用法::

    ResourceBlocker.apply_to_page(page, whitelist_domains=["qrcode.example.com"])
"""
from typing import Optional, Sequence

DEFAULT_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})

DEFAULT_BLOCKED_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".avif",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".webm", ".mp3", ".wav", ".ogg", ".m4a", ".m4v", ".mov", ".avi",
    ".pdf", ".zip", ".rar", ".7z",
})


class ResourceBlocker:
    """通过 page.route() 拦截不必要的资源。"""

    def __init__(
        self,
        resource_types: Optional[Sequence[str]] = None,
        extensions: Optional[Sequence[str]] = None,
        whitelist_domains: Optional[Sequence[str]] = None,
    ):
        self.resource_types = frozenset(resource_types or DEFAULT_BLOCKED_RESOURCE_TYPES)
        self.extensions = frozenset(extensions or DEFAULT_BLOCKED_EXTENSIONS)
        self.whitelist_domains = tuple(whitelist_domains or [])

    def _is_whitelisted(self, url: str) -> bool:
        if not self.whitelist_domains:
            return False
        return any(domain in url for domain in self.whitelist_domains)

    def apply(self, page) -> None:
        blocker = self

        def handler(route):
            req = route.request
            if blocker.whitelist_domains and blocker._is_whitelisted(req.url):
                route.continue_()
                return
            if req.resource_type in blocker.resource_types:
                route.abort()
                return
            url_lower = req.url.lower().split("?")[0]
            for ext in blocker.extensions:
                if url_lower.endswith(ext):
                    route.abort()
                    return
            route.continue_()

        page.route("**/*", handler)

    @classmethod
    def apply_to_page(
        cls,
        page,
        whitelist_domains: Optional[Sequence[str]] = None,
    ) -> "ResourceBlocker":
        blocker = cls(whitelist_domains=whitelist_domains)
        blocker.apply(page)
        return blocker


def block_unnecessary_resources(
    page,
    whitelist_domains: Optional[Sequence[str]] = None,
) -> ResourceBlocker:
    """便捷别名。"""
    return ResourceBlocker.apply_to_page(page, whitelist_domains=whitelist_domains)
