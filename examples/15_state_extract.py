"""示例 15：从 SSR 状态直接提数据（不用浏览器）。

很多站点的首屏数据不在 DOM 里，而在全局变量里：

    <script id="__NEXT_DATA__" type="application/json">{...}</script>
    window.__INITIAL_STATE__ = {...}
    window.__NUXT__ = {...}

`extract_state()` 用标准库做括号配对，把这些 JSON 取出来。
比解析 DOM 稳，也比开浏览器便宜一个量级。

默认跑内置 HTML；加 --live 请求真实站点（番茄小说）。

运行::

    python examples/15_state_extract.py
    python examples/15_state_extract.py --live
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.page_shape import classify_page_shape
from anti_crawl_kit.state_extract import extract_state

DEMO_HTML = """
<html>
  <body>
    <div id="root"></div>
    <script id="__NEXT_DATA__" type="application/json">
      {"props":{"pageProps":{"items":[
        {"id":1,"title":"第一篇","author":"张三"},
        {"id":2,"title":"第二篇","author":"李四"}
      ]}}}
    </script>
  </body>
</html>
"""


def demo_offline() -> int:
    shape = classify_page_shape(DEMO_HTML)
    print("=== 形态判断 ===")
    print(f"kind={shape.kind} states={shape.states}")
    print(f"advice={shape.advice}")

    result = extract_state(DEMO_HTML)
    print("\n=== 状态提取 ===")
    print(f"ok={result.ok} key={result.key} error={result.error}")
    if result.ok:
        items = result.data["props"]["pageProps"]["items"]
        print(f"items={len(items)}")
        for item in items:
            print(f"  {item['id']} {item['title']} / {item['author']}")
    return 0


def demo_live() -> int:
    from anti_crawl_kit.http import AntiRiskHttpClient

    url = "https://fanqienovel.com/"
    print(f"=== 请求 {url} ===")
    response = AntiRiskHttpClient("example15", timeout=15, max_retries=1).get_text(url)
    print(f"status={response.status_code} bytes={len(response.text)} risk={response.risk.kind}")

    shape = classify_page_shape(response.text, status=response.status_code)
    print(f"shape={shape.kind} states={shape.states}")

    result = extract_state(response.text)
    print(f"\n=== 状态提取 ok={result.ok} key={result.key} error={result.error}")
    if not result.ok:
        print("提取失败：IIFE 或页面被拦，改用浏览器读全局变量")
        return 1

    home = result.data.get("home") or {}
    print(f"home keys: {list(home.keys())[:10]}")
    for key in ("writerList", "updateList", "editorList", "weekList"):
        value = home.get(key)
        if isinstance(value, list):
            print(f"  {key}: {len(value)} 条")
            if value and isinstance(value[0], dict):
                print(f"    首条字段: {list(value[0].keys())[:8]}")
    return 0


def main() -> int:
    if "--live" in sys.argv:
        return demo_live()
    return demo_offline()


if __name__ == "__main__":
    raise SystemExit(main())
