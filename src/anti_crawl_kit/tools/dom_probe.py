"""DOM / 接口探测工具 —— 新平台接入第一步。

功能：
  1. 打开目标页，输出标题、URL、前端框架、__INITIAL_STATE__ 顶层键
  2. 列出页面实际请求过的 JSON 接口（Performance API）
  3. 统计重复 DOM 结构，推荐「列表容器」候选选择器
  4. 输出页面主要链接（用于确认卡片链接字段）

用法::

    python tools/dom_probe.py --url "https://quotes.toscrape.com/"
    python tools/dom_probe.py --url "https://www.bilibili.com" --headless
    python tools/dom_probe.py --url "https://example.com" --wait 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许直接运行（未 pip install 时）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

CANDIDATE_JS = """() => {
    var counts = {};
    var samples = {};
    var all = document.querySelectorAll('body *');
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (el.children.length < 2) continue;
        var tag = el.tagName.toLowerCase();
        var cls = '';
        try {
            var c = el.className;
            if (typeof c === 'string' && c.trim()) {
                cls = '.' + c.trim().split(/\\s+/).slice(0, 2).join('.');
            }
        } catch (e) {}
        var key = tag + cls;
        counts[key] = (counts[key] || 0) + 1;
        if (!samples[key]) {
            samples[key] = (el.innerText || '').trim().slice(0, 60);
        }
    }
    return Object.keys(counts)
        .filter(function(k) { return counts[k] >= 3; })
        .sort(function(a, b) { return counts[b] - counts[a]; })
        .slice(0, 25)
        .map(function(k) { return { selector: k, count: counts[k], sample: samples[k] }; });
}"""


def main() -> int:
    parser = argparse.ArgumentParser(description="DOM / API 探测")
    parser.add_argument("--url", required=True)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--wait", type=float, default=3.0, help="页面加载后等待秒数")
    parser.add_argument("--data-dir", default="", help="复用登录态的 profile 目录")
    parser.add_argument("--out", default="", help="把完整结果写入 JSON 文件")
    args = parser.parse_args()

    from anti_crawl_kit.api_discovery import ApiDiscoverer
    from anti_crawl_kit.session import BrowserSession

    with BrowserSession(
        headless=args.headless,
        data_dir=args.data_dir or None,
        anti_crawl=False,
    ) as page:
        page.goto(args.url, timeout=45000)
        page.wait_for_timeout(int(args.wait * 1000))

        print("=" * 70)
        print("URL   :", page.url)
        print("TITLE :", page.title())
        print("=" * 70)

        discoverer = ApiDiscoverer(page, deep_probe=False)
        framework = discoverer.detect_framework()
        print("\n[框架检测]")
        print(json.dumps(framework, ensure_ascii=False, indent=2))

        state = discoverer._extract_initial_state()
        if isinstance(state, dict):
            print("\n[__INITIAL_STATE__ 顶层键]")
            for key, value in state.items():
                kind = type(value).__name__
                size = len(value) if isinstance(value, (list, dict)) else ""
                print(f"  - {key}: {kind} {size}")
        elif state is not None:
            print("\n[__INITIAL_STATE__] 类型:", type(state).__name__)

        print("\n[页面请求过的候选接口]")
        for item in discoverer._network_urls()[:30]:
            print("  -", item.get("url"))

        print("\n[重复 DOM 结构候选（数量 >= 3）]")
        for item in page.evaluate(CANDIDATE_JS):
            print(f"  {item['count']:>4}x  {item['selector']}")
            print(f"         示例: {item['sample']}")

        print("\n[页面主要链接（前 20）]")
        links = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .slice(0, 40)
                .map(a => ({ text: (a.innerText || '').trim().slice(0, 50), href: a.href }))
                .filter(x => x.text)"""
        )
        for link in links[:20]:
            print(f"  - {link['text']} -> {link['href']}")

        if args.out:
            payload = {
                "url": page.url,
                "title": page.title(),
                "framework": framework,
                "initial_state_keys": list(state.keys()) if isinstance(state, dict) else [],
                "network_urls": discoverer._network_urls(),
                "dom_candidates": page.evaluate(CANDIDATE_JS),
                "links": links,
            }
            Path(args.out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print("\n[OK] 已写入", args.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
