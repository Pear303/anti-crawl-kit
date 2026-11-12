"""从 JS 包里找真实接口 —— 命令行入口。

为什么需要它：
    很多页面 HTML 里只有空壳，数字/列表由前端 JS 调接口拉取。
    与其上浏览器渲染，不如直接读 JS 包，把接口地址找出来，
    然后用 requests / curl_cffi 直接打。

用法::

    # 1) 直接给 URL（会先抓 HTML，再抓 JS）
    python tools/js_api_hunt.py --url "https://www.baidu.com/my/index" \\
        --cookie "BDUSS=xxx" --field fansNum --field subscribeNum

    # 2) 已有 HTML（比如从 DevTools 保存的）
    python tools/js_api_hunt.py --html page.html \\
        --base-url "https://www.baidu.com/my/index" --field fansNum

    # 3) 只列 script，不下载 JS
    python tools/js_api_hunt.py --url "https://example.com" --no-fetch

    # 4) 输出 JSON，方便后续脚本消费
    python tools/js_api_hunt.py --url "..." --out output/js_hunt.json

退出码：
    0  找到至少一个接口候选
    1  没找到接口候选
    2  页面/JS 全部下载失败
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from anti_crawl_kit.js_bundle import (
    extract_script_urls,
    fetch_text,
    hunt_api,
    parse_cookie_header,
)


def _collect_browser_scripts(
    page_url: str,
    cookies: dict[str, str] | None,
    *,
    timeout: int = 15,
    headless: bool = True,
) -> list[str]:
    """用真实浏览器打开页面，枚举 Network 里实际加载的全部 .js。

    只加载一次页面，不渲染等待、不滚动；拿到 URL 后仍由 HTTP 下载分析。
    这是补「HTML 里只有一个 script、其余全是动态 import」的唯一可靠办法。
    """
    try:
        from anti_crawl_kit import BrowserSession
    except Exception as exc:  # pragma: no cover - 依赖缺失时的提示
        print(f"[warn] 浏览器枚举不可用: {exc}", file=sys.stderr)
        return []

    urls: list[str] = []
    try:
        with BrowserSession(
            headless=headless,
            anti_crawl=False,
            block_resources=False,
        ) as page:
            if cookies:
                # 用 origin（scheme://host，不带路径）注入 cookie：
                # 形如 __Host-xxx 的 cookie 要求 path="/"，若用带路径的 page_url
                # 会被浏览器判定为 "Invalid cookie fields" 导致整批注入失败。
                _parts = urlsplit(page_url)
                origin = f"{_parts.scheme}://{_parts.netloc}"
                for name, value in cookies.items():
                    try:
                        page.context.add_cookies(
                            [{"name": name, "value": value, "url": origin}]
                        )
                    except Exception:
                        # 单颗 cookie 非法（空值/非法字符）不影响其余 cookie 注入
                        continue

            def on_response(response) -> None:
                url = response.url
                if ".js" in url.split("?", 1)[0] and url not in urls:
                    urls.append(url)

            page.raw.on("response", on_response)
            page.goto(
                page_url,
                wait_until="domcontentloaded",
                timeout=timeout * 1000,
                max_retries=1,
            )
            page.wait_for_timeout(4000)
    except Exception as exc:  # pragma: no cover - 真实浏览器环境
        print(f"[warn] 浏览器枚举失败: {exc}", file=sys.stderr)
    return urls


def _print_findings(result, *, limit: int = 30) -> None:
    print(result.summary())
    print()

    if result.base_urls:
        print("── baseURL ─────────────────────────────────────")
        for item in result.base_urls:
            print(f"  {item.value}")
            print(f"    来源: {item.source}")
            print(f"    上下文: {item.context[:220]}")
        print()

    if result.endpoints:
        shown = result.endpoints[:limit] if limit > 0 else result.endpoints
        print(f"── 接口候选（显示 {len(shown)}/{len(result.endpoints)}）────────")
        for item in shown:
            method = item.method or "GET"
            print(f"  [{method}] {item.resolved}")
            if item.resolved != item.value:
                print(f"    原始路径: {item.value}")
            print(f"    来源: {item.source}")
            print(f"    上下文: {item.context[:220]}")
        if len(shown) < len(result.endpoints):
            print(f"  ... 还有 {len(result.endpoints) - len(shown)} 个，用 --limit 0 显示全部")
        print()

    if result.field_hits:
        print("── 字段命中 ────────────────────────────────────")
        for item in result.field_hits:
            print(f"  {item.value} @ {item.source}")
            print(f"    {item.context[:260]}")
        print()

    if result.failed_scripts:
        print("── 下载失败 ────────────────────────────────────")
        for item in result.failed_scripts:
            print(f"  {item.get('url')}  status={item.get('status')}  {item.get('error')}")
        print()

    if result.endpoints:
        print("── 下一步 ──────────────────────────────────────")
        print("  1. 带登录 cookie 直接请求候选接口，确认返回 JSON：")
        print("     curl -H 'Cookie: ...' '<接口 URL>'")
        print("  2. 返回 200 且字段对得上 → L1 纯 HTTP，零浏览器。")
        print("  3. 返回 401/403 → 先补登录态；返回空 body / result=50 → 签名失败。")
        print("  4. 签名失败再回浏览器拦截（examples/04），但别停在 L3：")
        print("     从 Network 导出请求，做「签名剥离测试」，见 docs/22。")
        print()
    else:
        print("没找到接口候选。可能原因：")
        print("  - JS 是动态 import 的 chunk，加 --loose 或手动看 Network")
        print("  - 接口路径是拼接出来的（如 '/user/' + id），需要读上下文")
        print("  - 接口在 Web Worker / WASM 里")
        print("  - 页面本身是 SSR，数据在 HTML 里（用 dom_probe.py 看）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 JS 包里找真实接口",
        epilog=(
            "退出码：0=找到接口候选；1=未找到（正常业务结果）；"
            "2=运行错误（页面/JS 抓取失败或参数错误）"
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="页面 URL")
    source.add_argument("--html", help="本地 HTML 文件路径")
    parser.add_argument(
        "--base-url",
        default="",
        help="配合 --html 使用，指定页面原始 URL（用于拼相对路径和 Referer）",
    )
    parser.add_argument(
        "--cookie",
        default="",
        help='Cookie 字符串，如 "BDUSS=xxx; STOKEN=yyy"',
    )
    parser.add_argument(
        "--cookie-file",
        default="",
        help="从文件读 Cookie（一行，或 Netscape cookies.txt 的简化版）",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        help="想在 JS 里定位的字段名，可重复，如 --field fansNum",
    )
    parser.add_argument("--referer", default="", help="下载 JS 时的 Referer，默认用页面 URL")
    parser.add_argument(
        "--api-host",
        default="",
        help="相对路径接口的真实域名，如 api.bilibili.com（默认用页面 origin）",
    )
    parser.add_argument(
        "--max-scripts",
        type=int,
        default=80,
        help="最多下载多少个 JS（默认 80，覆盖 SPA 全部 chunk）",
    )
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--no-fetch", action="store_true", help="只列 script，不下载 JS")
    parser.add_argument("--loose", action="store_true", help="松散模式，抓孤立的 '/xxx' 字符串")
    parser.add_argument(
        "--browser",
        choices=("never", "auto", "always"),
        default="auto",
        help="用浏览器枚举页面实际加载的 JS：auto=静态发现≤2 个时自动；always=总是；never=只用 HTTP",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="浏览器枚举时使用有头模式（默认无头）",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="只显示路径/字段名包含该关键字的候选，如 --filter popular",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="控制台最多显示多少条候选（JSON 输出仍保留全部）",
    )
    parser.add_argument(
        "--no-all-chunks",
        "--no-follow-loaders",
        dest="all_chunks",
        action="store_false",
        help="不递归 JS 里引用的动态 chunk（只扫入口）",
    )
    parser.add_argument("--out", default="", help="把结果写入 JSON 文件")
    args = parser.parse_args(argv)

    cookie_raw = args.cookie
    if args.cookie_file:
        cookie_raw = Path(args.cookie_file).read_text(encoding="utf-8").strip()
    cookies = parse_cookie_header(cookie_raw)

    html: str | None = None
    fetch_failed = False
    page_url = args.base_url or args.url
    if args.html:
        try:
            html = Path(args.html).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"读取 HTML 失败: {exc}", file=sys.stderr)
            return 2
        if not page_url:
            print("使用 --html 时必须提供 --base-url（用于拼相对路径）", file=sys.stderr)
            return 2
    elif not page_url:
        print("必须提供 --url 或 --html + --base-url", file=sys.stderr)
        return 2
    elif not args.no_fetch:
        # 先抓一次 HTML，用于判断「静态入口是不是只有 1~2 个」。
        # 真正下载 JS 仍走 hunt_api，这里不重复分析。
        status, html_text, error = fetch_text(
            page_url,
            referer=args.referer or page_url,
            cookies=cookies or None,
            timeout=args.timeout,
        )
        html = html_text or ""
        if not html_text:
            fetch_failed = True
            print(
                f"[warn] 页面 HTML 抓取失败: status={status} error={error}",
                file=sys.stderr,
            )

    static_scripts = extract_script_urls(html or "", page_url)
    browser_scripts: list[str] = []
    use_browser = args.browser == "always" or (
        args.browser == "auto"
        and args.html is None
        and not args.no_fetch
        and len(static_scripts) <= 2
    )
    if use_browser:
        print(
            f"[info] 静态只发现 {len(static_scripts)} 个 JS，用浏览器枚举页面实际加载的全部资源…",
            file=sys.stderr,
        )
        browser_scripts = _collect_browser_scripts(
            page_url,
            cookies or None,
            timeout=args.timeout,
            headless=not args.headed,
        )
        print(f"[info] 浏览器发现 {len(browser_scripts)} 个 JS", file=sys.stderr)

    def _progress(done: int, total: int, _src: str) -> None:
        # 长任务给个进度，避免被误认为卡死；写 stderr，不污染 stdout 的候选列表
        print(f"[hunt] 已扫描 {done}/{total} 个 JS", file=sys.stderr, flush=True)

    result = hunt_api(
        page_url=page_url,
        html=html,
        cookies=cookies or None,
        fields=args.field,
        referer=args.referer or page_url,
        max_scripts=args.max_scripts,
        timeout=args.timeout,
        fetch=not args.no_fetch,
        loose=args.loose,
        follow_loaders=args.all_chunks,
        api_host=args.api_host,
        extra_scripts=browser_scripts,
        progress=_progress if not args.no_fetch else None,
    )

    if args.filter:
        kw = args.filter.lower()
        result.endpoints = [
            item
            for item in result.endpoints
            if kw in (item.resolved or "").lower() or kw in (item.value or "").lower()
        ]
        result.field_hits = [
            item
            for item in result.field_hits
            if kw in (item.value or "").lower() or kw in (item.context or "").lower()
        ]

    _print_findings(result, limit=args.limit)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"结果已写入 {out}")

    if result.endpoints:
        return 0
    # 页面没抓到，或所有 JS 都下载失败：属于运行错误，不是「没找到」
    if fetch_failed or (result.failed_scripts and not result.scripts):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
