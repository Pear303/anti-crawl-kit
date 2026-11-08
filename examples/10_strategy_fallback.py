"""示例 10：多策略降级 —— SSR → XHR → DOM，逐级兜底。

为什么需要：
    平台改版时，通常只坏一条路。只写一条路，改版即死；
    有三条路，坏一条还能跑，而且日志能告诉你哪条坏了。

本示例在 quotes.toscrape.com 上演示：
    1. SSR 状态（__INITIAL_STATE__ / __NEXT_DATA__）—— 该站没有，失败
    2. XHR 资源（performance entries）—— 该站没有 /api/，失败
    3. DOM 解析 —— 成功

关键：run_strategies 会记录每条策略的 ok / items / error，
     而不是只返回最终列表。这样出问题时知道是哪条挂了。

运行::

    python examples/10_strategy_fallback.py
    python examples/10_strategy_fallback.py --headless
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit import BrowserSession
from anti_crawl_kit.strategy import run_strategies

URL = "https://quotes.toscrape.com/"

SSR_JS = """() => window.__INITIAL_STATE__ || window.__NEXT_DATA__ || null"""

XHR_JS = """() => performance.getEntriesByType('resource')
    .map(function (e) { return e.name; })
    .filter(function (u) { return u.indexOf('/api/') !== -1; })"""

DOM_JS = """() => Array.from(document.querySelectorAll('div.quote')).map(function (el) {
    var text = el.querySelector('.text');
    var author = el.querySelector('.author');
    return {
        text: text ? text.innerText.trim() : '',
        author: author ? author.innerText.trim() : ''
    };
})"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--out", default="output/strategy_fallback.json")
    args = parser.parse_args()

    with BrowserSession(
        headless=args.headless,
        data_dir="./profiles/demo",
        anti_crawl=False,
    ) as page:
        page.goto(URL, timeout=60000)

        def ssr_state() -> list:
            state = page.evaluate(SSR_JS, isolated_context=False)
            if isinstance(state, dict):
                return state.get("quotes") or []
            return []

        def xhr_api() -> list:
            urls = page.evaluate(XHR_JS) or []
            return [{"url": u} for u in urls]

        def dom() -> list:
            return page.evaluate(DOM_JS) or []

        results = run_strategies(
            [
                ("ssr_state", ssr_state),
                ("xhr_api", xhr_api),
                ("dom", dom),
            ],
            stop_on_first_items=True,
        )

    print("\n策略执行结果：")
    for item in results:
        print(
            f"  [{item.name:12s}] ok={item.ok} "
            f"items={len(item.items):3d} "
            f"error={item.error or '-'}"
        )

    winner = next((item for item in results if item.items), None)
    if winner is None:
        print("\n全部策略为空：大概率改版、被风控，或页面结构完全变了。")
        print("下一步：python tools/dom_probe.py --url " + URL)
        return 1

    print(f"\n命中策略: {winner.name}，{len(winner.items)} 条")
    for item in winner.items[:3]:
        print(" ", json.dumps(item, ensure_ascii=False))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "url": URL,
                "winner": winner.name,
                "strategies": [
                    {"name": r.name, "ok": r.ok, "items": r.items, "error": r.error}
                    for r in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
