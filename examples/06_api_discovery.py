"""示例 06：新平台接口发现 —— 打开页面，自动列出候选 API / SSR 数据。

运行::

    python examples/06_api_discovery.py --url "https://www.bilibili.com"
    python examples/06_api_discovery.py --url "https://example.com" --no-deep

输出 manifest 可直接作为新平台接入的起点。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.api_discovery import ApiDiscoverer
from anti_crawl_kit.session import BrowserSession


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-deep", action="store_true", help="不做接口 fetch 验证")
    parser.add_argument("--out", default="output/api_manifest.json")
    args = parser.parse_args()

    with BrowserSession(
        headless=args.headless,
        data_dir=None,
        anti_crawl=False,
    ) as page:
        manifest = ApiDiscoverer(page, deep_probe=not args.no_deep).discover(args.url)

    print("\n框架:", manifest["framework"])
    print("初始状态:", "有" if manifest["initial_state"] else "无")
    print("Store 数量:", len(manifest["stores"]))
    print("候选接口:")
    for ep in manifest["api_endpoints"][:20]:
        print("  -", ep.get("url"))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n已写入", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
