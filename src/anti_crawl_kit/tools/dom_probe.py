"""DOM / 接口探测工具 —— 新平台接入第一步。

功能：
  1. 打开目标页，输出标题、URL、前端框架、__INITIAL_STATE__ 顶层键
  2. 列出页面实际请求过的 JSON 接口（Performance API）
  3. 统计重复 DOM 结构，推荐「列表容器」候选选择器
  4. 对候选容器再展开一层，给出字段选择器候选（供生成评估配置）
  5. 输出页面主要链接（用于确认卡片链接字段）

设计：``probe_dom()`` 是可复用函数（MCP / recipe / 测试都能调），
``main()`` 只负责参数解析和打印。

用法::

    python tools/dom_probe.py --url "https://quotes.toscrape.com/"
    python tools/dom_probe.py --url "https://www.bilibili.com" --headless
    python tools/dom_probe.py --url "https://example.com" --wait 5 --out output/dom.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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

# 对每个候选容器，取第一个实例，展开它的子节点，给出相对选择器候选。
# 这是「从探测结果直接生成 field_selectors」的依据，避免 Agent 手写选择器。
OUTLINE_JS = """(selectors) => {
    function rel(node) {
        var tag = node.tagName.toLowerCase();
        var cls = '';
        try {
            var c = node.className;
            if (typeof c === 'string' && c.trim()) {
                cls = '.' + c.trim().split(/\\s+/).slice(0, 2).join('.');
            }
        } catch (e) {}
        return tag + cls;
    }
    var out = [];
    for (var i = 0; i < selectors.length; i++) {
        var sel = selectors[i];
        var root = null;
        try { root = document.querySelector(sel); } catch (e) { continue; }
        if (!root) continue;
        var nodes = Array.prototype.slice.call(root.querySelectorAll('*'), 0, 40);
        var outline = [];
        for (var j = 0; j < nodes.length; j++) {
            var n = nodes[j];
            var text = (n.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
            var href = n.getAttribute('href') || '';
            var src = n.getAttribute('src') || '';
            if (!text && !href && !src) continue;
            outline.push({ selector: rel(n), text: text, href: href, src: src });
        }
        out.push({ selector: sel, outline: outline });
    }
    return out;
}"""

_LINKS_JS = """() => Array.from(document.querySelectorAll('a[href]'))
    .slice(0, 40)
    .map(a => ({ text: (a.innerText || '').trim().slice(0, 50), href: a.href }))
    .filter(x => x.text)"""


def _collect(
    page: Any,
    url: str,
    *,
    wait: float,
    max_candidates: int,
    outline_top: int,
) -> dict[str, Any]:
    """在已打开的 page 上做探测，返回可 JSON 序列化的结果。"""
    from anti_crawl_kit.api_discovery import ApiDiscoverer

    page.goto(url, timeout=45000)
    page.wait_for_timeout(int(wait * 1000))

    discoverer = ApiDiscoverer(page, deep_probe=False)
    framework = discoverer.detect_framework()
    state = discoverer._extract_initial_state()
    candidates = page.evaluate(CANDIDATE_JS)[:max_candidates]

    # 只对最靠前的几个候选展开字段结构，避免在超大页面上耗时。
    top = [item["selector"] for item in candidates[: max(0, outline_top)]]
    outlines = {
        item["selector"]: item["outline"]
        for item in (page.evaluate(OUTLINE_JS, top) if top else [])
    }
    item_candidates = [
        {
            "selector": item["selector"],
            "count": item["count"],
            "sample": item["sample"],
            "outline": outlines.get(item["selector"], []),
        }
        for item in candidates
    ]

    links = page.evaluate(_LINKS_JS)

    return {
        "ok": True,
        "tool": "dom_probe",
        "url": page.url,
        "title": page.title(),
        "framework": framework,
        "initial_state_keys": list(state.keys()) if isinstance(state, dict) else [],
        "network_urls": discoverer._network_urls(),
        "dom_candidates": candidates,
        "item_candidates": item_candidates,
        "links": links,
    }


def probe_dom(
    url: str,
    *,
    headless: bool = False,
    wait: float = 3.0,
    data_dir: str = "",
    skip_version_check: bool = False,
    max_candidates: int = 15,
    outline_top: int = 3,
    page: Any = None,
) -> dict[str, Any]:
    """打开页面做 DOM/接口探测。

    ``page`` 非空时复用它（测试注入），否则自己开一个 ``BrowserSession``。
    """
    if page is not None:
        return _collect(
            page, url, wait=wait, max_candidates=max_candidates, outline_top=outline_top
        )

    from anti_crawl_kit.session import BrowserSession

    with BrowserSession(
        headless=headless,
        data_dir=data_dir or None,
        anti_crawl=False,
        skip_version_check=skip_version_check,
    ) as live_page:
        return _collect(
            live_page,
            url,
            wait=wait,
            max_candidates=max_candidates,
            outline_top=outline_top,
        )


def _print_report(payload: dict[str, Any]) -> None:
    print("=" * 70)
    print("URL   :", payload["url"])
    print("TITLE :", payload["title"])
    print("=" * 70)

    print("\n[框架检测]")
    print(json.dumps(payload["framework"], ensure_ascii=False, indent=2))

    if payload["initial_state_keys"]:
        print("\n[__INITIAL_STATE__ 顶层键]")
        for key in payload["initial_state_keys"]:
            print("  -", key)

    print("\n[页面请求过的候选接口]")
    for item in payload["network_urls"][:30]:
        print("  -", item.get("url"))

    print("\n[重复 DOM 结构候选（数量 >= 3）]")
    for item in payload["item_candidates"]:
        print(f"  {item['count']:>4}x  {item['selector']}")
        print(f"         示例: {item['sample']}")

    print("\n[页面主要链接（前 20）]")
    for link in payload["links"][:20]:
        print(f"  - {link['text']} -> {link['href']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DOM / API 探测")
    parser.add_argument("--url", required=True)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--wait", type=float, default=3.0, help="页面加载后等待秒数")
    parser.add_argument("--data-dir", default="", help="复用登录态的 profile 目录")
    parser.add_argument("--out", default="", help="把完整结果写入 JSON 文件")
    parser.add_argument(
        "--skip-version-check",
        action="store_true",
        help="跳过 Chrome 与 patchright 大版本检查（确认兼容时使用）",
    )
    args = parser.parse_args(argv)

    payload = probe_dom(
        args.url,
        headless=args.headless,
        wait=args.wait,
        data_dir=args.data_dir,
        skip_version_check=args.skip_version_check,
    )
    _print_report(payload)

    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n[OK] 已写入", args.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
