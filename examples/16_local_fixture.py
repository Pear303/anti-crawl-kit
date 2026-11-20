"""示例 16：本地 fixture —— 完全离线跑形态判断、DOM 提取和分页。

为什么需要它
------------
大多数示例要么要联网，要么要扫码登录。第一次上手时，最想确认的是
「这套东西能不能跑」，而不是「真实站点能不能采」。

这个示例只读 ``examples/fixtures/`` 下的三个静态 HTML：

- ``list.html``     服务端渲染好的列表页，有卡片、分页、摘要
- ``spa_shell.html`` 只有 <div id="app"></div> 的空壳
- ``captcha.html``  验证墙

不需要浏览器、不需要网络、不需要任何额外依赖。

运行::

    python examples/16_local_fixture.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.console import install_safe_print
from anti_crawl_kit.dom_extract import (
    count_matches,
    extract_records,
    node_attr,
    node_text,
    parse_html,
    select_all,
)
from anti_crawl_kit.page_shape import classify_page_shape

install_safe_print()  # Windows GBK 控制台下 ¥ 等字符不会崩

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 卡片字段：选择器 + 是否取属性
FIELDS = {
    "title": {"selector": "h2.title"},
    "href": {"selector": "h2.title a", "attr": "href"},
    "price": {"selector": "p.price"},
    "tags": {"selector": ".tag", "all": True},
}


def _section(title: str) -> None:
    print(f"\n== {title} ==")


def main() -> int:
    failures = 0
    list_html = (FIXTURES / "list.html").read_text(encoding="utf-8")
    shell_html = (FIXTURES / "spa_shell.html").read_text(encoding="utf-8")
    captcha_html = (FIXTURES / "captcha.html").read_text(encoding="utf-8")

    _section("1. 页面形态：先判断，再写选择器")
    expected = {
        "list.html": {"ssr", "unknown"},
        "spa_shell.html": {"spa_shell"},
        "captcha.html": {"captcha_wall"},
    }
    for name, html in (
        ("list.html", list_html),
        ("spa_shell.html", shell_html),
        ("captcha.html", captcha_html),
    ):
        shape = classify_page_shape(html)
        print(f"{name:16} -> {shape.kind:12} {shape.advice}")
        if shape.kind not in expected[name]:
            print(f"[FAIL] {name} 形态判断不符合预期: {shape.kind}")
            failures += 1

    _section("2. DOM 提取：卡片 + 字段")
    cards = count_matches(list_html, "article.card")
    records = extract_records(list_html, "article.card", FIELDS)
    print(f"article.card 命中 {cards} 条，抽出 {len(records)} 条")
    for row in records[:3]:
        print(f"  {row['title']} · {row['price']} · {'/'.join(row['tags'])}")
    print("  ...")
    if cards != 10 or len(records) != cards:
        print("[FAIL] 卡片数量或记录数量不对")
        failures += 1

    _section("3. 分页：拿下一页，而不是硬拼页码")
    root = parse_html(list_html)
    links = select_all(root, "nav.pagination a")
    pages = [(node_text(link), node_attr(link, "href")) for link in links]
    for text, href in pages:
        print(f"  {text:6} -> {href}")
    next_link = select_all(root, "a.next")
    next_href = node_attr(next_link[0], "href") if next_link else ""
    print(f"下一页: {next_href}")
    if next_href != "?page=2":
        print("[FAIL] 没有拿到下一页链接")
        failures += 1

    _section("4. 静默空结果：0 条不等于成功")
    stale = count_matches(list_html, "div.product-item")
    print(f"旧选择器 div.product-item 命中 {stale} 条")
    if stale == 0:
        print("[WARN] 0 条时要先确认：选择器是否失效、页面是否空壳、是否被拦截")
        print("       不要直接把 0 条当成「今天没有新数据」")
    else:
        print("[FAIL] 旧选择器不应该命中")
        failures += 1

    _section("5. 空壳页：数据不在 HTML 里")
    shell = classify_page_shape(shell_html)
    print(f"spa_shell.html -> {shell.kind} · 可见字符 {shell.visible_chars}")
    print("  下一步：ack-hunt 从 JS 包找接口，或浏览器拦 XHR；不要写 DOM 选择器")
    if shell.kind != "spa_shell":
        print("[FAIL] 空壳页判断不符合预期")
        failures += 1

    _section("6. 验证墙：识别出来就停")
    captcha = classify_page_shape(captcha_html)
    print(f"captcha.html -> {captcha.kind} · {captcha.advice}")
    if captcha.kind != "captcha_wall":
        print("[FAIL] 验证墙判断不符合预期")
        failures += 1

    if failures:
        print(f"\n[FAIL] {failures} 项不符合预期")
        return 1
    print("\n[OK] 全部通过：没有联网，也没有启动浏览器")
    print("[INFO] 换成你自己的静态页面，就能当回归测试用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
