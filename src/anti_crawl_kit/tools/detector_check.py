"""反检测自检工具 —— 打开检测站点，输出结构化结果 + 截图。

检测站点：
  - bot.sannysoft.com          基础 webdriver / 指纹项
  - bot-detector.rebrowser.net CDP / Runtime 泄漏
  - creepjs.com                综合指纹一致性

用法::

    python tools/detector_check.py
    python tools/detector_check.py --json
    python tools/detector_check.py --only sannysoft --headless

输出：
    人类可读摘要，或 --json 输出结构化结果。
    截图保存在 output/detector/。

判定口径（保守）：
    webdriver_false    navigator.webdriver === false
    ua_present         UA 非空
    plugins_nonzero    navigator.plugins.length > 0
    chrome_object      window.chrome 存在

⚠ 这只是「基础项」自检，不等于「不会被检测」。
   真实站点还会看 IP、行为、账号历史、TLS 指纹一致性。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

TARGETS = [
    ("sannysoft", "https://bot.sannysoft.com/"),
    ("rebrowser", "https://bot-detector.rebrowser.net/"),
    ("creepjs", "https://creepjs.com/"),
]

_PROBE_JS = """() => {
  var body = document.body ? (document.body.innerText || '') : '';
  return {
    webdriver: navigator.webdriver,
    ua: navigator.userAgent,
    plugins: navigator.plugins ? navigator.plugins.length : -1,
    languages: navigator.languages || [],
    has_chrome: typeof window.chrome !== 'undefined',
    body_has_failed: /\\bfailed\\b/i.test(body),
    body_chars: body.length
  };
}"""


def _checks(data: dict) -> dict:
    ua = data.get("ua") or ""
    return {
        "webdriver_false": data.get("webdriver") is False,
        "ua_present": bool(ua),
        # headless 模式 UA 会带 HeadlessChrome，本身就是检测信号
        "ua_not_headless": "HeadlessChrome" not in ua,
        "plugins_nonzero": (data.get("plugins") or 0) > 0,
        "chrome_object": bool(data.get("has_chrome")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="反检测自检")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--only", default="", help="只跑指定站点，如 sannysoft")
    parser.add_argument("--out", default="output/detector")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON")
    args = parser.parse_args()

    from anti_crawl_kit.session import BrowserSession

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []

    with BrowserSession(headless=args.headless, anti_crawl=False) as page:
        for name, url in TARGETS:
            if args.only and args.only != name:
                continue

            record: dict = {
                "site": name,
                "url": url,
                "ok": False,
                "checks": {},
                "data": {},
                "screenshot": "",
                "error": "",
            }

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                data = page.evaluate(_PROBE_JS, isolated_context=False)
                record["data"] = data if isinstance(data, dict) else {}
                record["checks"] = _checks(record["data"])
                record["ok"] = all(record["checks"].values())
            except Exception as exc:
                record["error"] = repr(exc)

            try:
                shot = out_dir / f"{name}_{'headless' if args.headless else 'headful'}.png"
                page.screenshot(path=str(shot), full_page=False)
                record["screenshot"] = str(shot)
            except Exception as exc:
                record["error"] = record["error"] or f"screenshot: {exc!r}"

            results.append(record)

    payload = {
        "ok": bool(results) and all(r["ok"] for r in results),
        "tool": "detector_check",
        "headless": args.headless,
        "results": results,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for record in results:
            data = record["data"]
            print(
                f"[{record['site']}] ok={record['ok']} "
                f"webdriver={data.get('webdriver')} "
                f"plugins={data.get('plugins')} "
                f"chrome={data.get('has_chrome')}"
            )
            if data.get("ua"):
                print(f"    ua={data['ua'][:100]}")
            if record["checks"]:
                failed = [k for k, v in record["checks"].items() if not v]
                print(f"    未通过: {failed or '无'}")
            if record["screenshot"]:
                print(f"    截图: {record['screenshot']}")
            if record["error"]:
                print(f"    错误: {record['error']}")
        print(f"\n整体: {'PASS' if payload['ok'] else 'CHECK'}")

    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
