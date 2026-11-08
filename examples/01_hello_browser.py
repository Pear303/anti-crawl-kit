"""示例 01：最小可用 —— 启动浏览器、导航、截图、检查反检测状态。

运行::

    python examples/01_hello_browser.py
    python examples/01_hello_browser.py --headless
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit import BrowserSession


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--url", default="https://bot.sannysoft.com/")
    args = parser.parse_args()

    with BrowserSession(
        headless=args.headless,
        data_dir="./profiles/hello",   # 持久化登录态；不需要可删
        anti_crawl=False,              # 单次访问不需要限频
    ) as page:
        page.goto(args.url, timeout=45000)
        page.wait_for_timeout(2000)

        print("标题:", page.title())
        print("URL :", page.url)

        report = page.evaluate(
            "() => ({ webdriver: navigator.webdriver, ua: navigator.userAgent, "
            "plugins: navigator.plugins.length, languages: navigator.languages })",
            isolated_context=False,
        )
        print("反检测状态:", report)

        Path("output").mkdir(exist_ok=True)
        page.screenshot(path="output/hello.png", full_page=False)
        print("截图: output/hello.png")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
