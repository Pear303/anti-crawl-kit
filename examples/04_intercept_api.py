"""示例 04：拦截页面 JSON 接口 —— 比解析 DOM 更稳更快。

思路：
    1. 先注册 response 监听（在导航之前！）
    2. 打开页面 / 触发滚动
    3. 从捕获的 JSON 里直接取结构化数据

目标：B 站热门列表（公开接口，无需登录）。

运行::

    python examples/04_intercept_api.py
    python examples/04_intercept_api.py --headless
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.session import BrowserSession


def main() -> int:
    # Windows 默认 GBK 控制台无法输出 emoji；统一成 UTF-8，遇到无法编码的字符用 ? 代替
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    with BrowserSession(
        headless=args.headless,
        data_dir="./profiles/bilibili",
        anti_crawl=False,
    ) as page:
        # 关键：监听必须在导航之前注册
        captured = page.capture_responses("/x/web-interface/popular")
        page.goto("https://www.bilibili.com/v/popular/all", timeout=60000)
        page.wait_for_timeout(5000)
        page.stop_capture("/x/web-interface/popular")

        print(f"捕获到 {len(captured)} 个响应")
        if not captured:
            print("未捕获到接口，可能页面结构变了；用 tools/dom_probe.py 重新探测。")
            return 1

        payload = captured[0]
        print("顶层键:", list(payload.keys()))
        data = payload.get("data") or {}
        items = data.get("list") or []
        print(f"列表条数: {len(items)}")

        rows = []
        for item in items[:10]:
            rows.append({
                "bvid": item.get("bvid"),
                "title": item.get("title"),
                "owner": (item.get("owner") or {}).get("name"),
                "view": (item.get("stat") or {}).get("view"),
                "like": (item.get("stat") or {}).get("like"),
            })
        print(json.dumps(rows, ensure_ascii=False, indent=2))

        Path("output").mkdir(exist_ok=True)
        Path("output/bilibili_popular.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
