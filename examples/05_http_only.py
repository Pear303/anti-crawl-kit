"""示例 05：纯 HTTP 直连（curl_cffi TLS 指纹伪装）。

适用：公开 JSON 接口、批量拉取、无需执行 JS 的场景。
优势：比浏览器快 10-50 倍，资源占用极低。

运行::

    python examples/05_http_only.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.headers import get_platform_headers
from anti_crawl_kit.http import AntiRiskHttpClient


def main() -> int:
    client = AntiRiskHttpClient(
        "bilibili",
        impersonate="chrome120",   # 伪装 Chrome TLS 指纹
        max_retries=3,
    )
    result = client.get_json(
        "https://api.bilibili.com/x/web-interface/popular",
        params={"ps": 20, "pn": 1},
        headers=get_platform_headers("bilibili"),
    )

    print("HTTP 状态:", result.status_code)
    print("风险分类:", result.risk)

    if result.risk.should_fallback_to_browser:
        print("→ 该接口已触发风控，应降级到浏览器执行（见 examples/04）。")
        return 1

    if not isinstance(result.data, dict):
        print("响应不是 JSON:", result.text[:200])
        return 1

    items = (result.data.get("data") or {}).get("list") or []
    print(f"拿到 {len(items)} 条")
    rows = [
        {
            "bvid": it.get("bvid"),
            "title": it.get("title"),
            "view": (it.get("stat") or {}).get("view"),
        }
        for it in items[:10]
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))

    Path("output").mkdir(exist_ok=True)
    Path("output/http_popular.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
