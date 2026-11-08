"""示例 12：数据源分层兜底 + 双跑校验。

演示三件事：

1. 主路径（API）网络失败 → 自动回退 HTML；
2. 403 风控 / 验证码 → 不回退，直接停；
3. 主路径成功 → 双跑对比，差异率超阈值告警。

运行::

    python examples/12_datasource_fallback.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit import AntiRiskHttpClient
from anti_crawl_kit.datasource import DataSourcePolicy, fetch_with_fallback

URL = "https://quotes.toscrape.com/"


def parse_quotes(client: AntiRiskHttpClient, *, tamper: bool = False) -> list[dict]:
    result = client.get_text(URL)
    if result.status_code != 200:
        raise RuntimeError(f"HTTP {result.status_code}")
    texts = re.findall(
        r'<span class="text"[^>]*>(.*?)</span>', result.text, re.S
    )
    authors = re.findall(
        r'<small class="author"[^>]*>(.*?)</small>', result.text, re.S
    )
    items = [
        {"title": text.strip(), "author": author.strip()}
        for text, author in zip(texts, authors)
    ]
    if tamper and items:
        items[0]["title"] = "被篡改的标题"
    return items


def make_fetchers(client: AntiRiskHttpClient, *, api_mode: str, html_tamper: bool = False):
    def api():
        if api_mode == "fail":
            raise RuntimeError("HTTP 503 timeout（模拟私有 API 挂了）")
        if api_mode == "blocked":
            raise RuntimeError("HTTP 403 blocked（模拟风控）")
        if api_mode == "signature":
            raise RuntimeError("a_bogus signature_failed（模拟签名失效）")
        return parse_quotes(client, tamper=False)

    def html():
        return parse_quotes(client, tamper=html_tamper)

    def browser():
        # 真实场景这里是 Playwright 渲染；示例里用同一份 HTML 代替
        return parse_quotes(client, tamper=False)

    return {"api": api, "html": html, "browser": browser}


def main() -> int:
    client = AntiRiskHttpClient("demo", timeout=20, max_retries=1)

    print("=== 场景 1：API 网络失败 → 回退 HTML ===")
    outcome = fetch_with_fallback(
        make_fetchers(client, api_mode="fail"),
        policy=DataSourcePolicy(
            primary="api", fallbacks=("html",), cross_check=False
        ),
    )
    print(outcome.summary())

    print("\n=== 场景 2：403 风控 → 不回退，直接停 ===")
    outcome = fetch_with_fallback(
        make_fetchers(client, api_mode="blocked"),
        policy=DataSourcePolicy(
            primary="api", fallbacks=("html",), cross_check=False
        ),
    )
    print(outcome.summary())

    print("\n=== 场景 3：签名失效 → 优先转浏览器 ===")
    outcome = fetch_with_fallback(
        make_fetchers(client, api_mode="signature"),
        policy=DataSourcePolicy(
            primary="api", fallbacks=("html",), cross_check=False
        ),
    )
    print(outcome.summary())

    print("\n=== 场景 4：主路径成功 + 双跑校验 ===")
    outcome = fetch_with_fallback(
        make_fetchers(client, api_mode="ok", html_tamper=True),
        policy=DataSourcePolicy(
            primary="api",
            fallbacks=("html",),
            cross_check=True,
            cross_check_n=5,
            diff_threshold=0.1,
        ),
    )
    print(outcome.summary())
    if outcome.cross_check and outcome.cross_check.get("alarm"):
        print("  → 告警：API 与 HTML 差异超过阈值，可能 API 返回旧数据")
        for detail in outcome.cross_check.get("details", [])[:3]:
            print(f"     #{detail['index']} api={detail['primary']!r} html={detail['fallback']!r}")

    print("\n=== 场景 5：WAF 识别（stop_reason + detection）===")

    def blocked_by_cloudflare():
        raise RuntimeError("403 Cloudflare Just a moment...")

    outcome = fetch_with_fallback(
        {"api": blocked_by_cloudflare, "html": lambda: [{"title": "不该被调用"}]},
        policy=DataSourcePolicy(primary="api", fallbacks=("html",), cross_check=False),
    )
    print(outcome.summary())
    print(f"  stop_reason={outcome.stop_reason} detection={outcome.detection}")
    print("  → 下游据此告警，而不是把拒绝当成空结果继续重试")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
