#!/usr/bin/env python3
"""最小 benchmark：同一批站点，比较 curl_cffi 与 patchright。

目的不是「证明浏览器更强」，而是量化三件事：

    1. 哪些站点 HTTP 直连就够，不需要开浏览器；
    2. 哪些站点 HTTP 被拦、必须上浏览器；
    3. 同一站点两种路径的耗时比（成本）。

用法::

    python tools/bench.py                     # 默认 3 站 × 2 工具
    python tools/bench.py --only http         # 只跑 HTTP
    python tools/bench.py --repeat 3          # 每站重复 3 次
    python tools/bench.py --headed            # 有头浏览器
    python tools/bench.py --out benchmarks/run1

输出：``<out>/report.md`` + ``<out>/results.json``
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from anti_crawl_kit.adaptive_selector import extract_features
from anti_crawl_kit.captcha import detect_captcha_page
from anti_crawl_kit.datasource import detect_waf


@dataclass(frozen=True)
class Site:
    name: str
    url: str
    selector: str = ""
    # HTML 小于这个字节数视为「没拿到有效页面」
    min_bytes: int = 2000


DEFAULT_SITES: tuple[Site, ...] = (
    Site("quotes", "https://quotes.toscrape.com/", "div.quote"),
    Site("books", "https://books.toscrape.com/", "article.product_pod"),
    Site("biquge", "https://www.biquge.mom/", ".book-card"),
)


@dataclass
class BenchResult:
    site: str
    tool: str
    ok: bool
    status: int
    elapsed_ms: int
    bytes: int
    items: int
    risk: str = ""
    detection: str = ""
    title: str = ""
    error: str = ""


@dataclass
class BenchReport:
    started_at: str
    env: dict[str, str]
    startup_ms: int = 0
    results: list[BenchResult] = field(default_factory=list)


# ── 工具 ────────────────────────────────────────────
def _count_in_html(html: str, selector: str) -> int:
    if not selector:
        return 0
    try:
        return len(extract_features(html, selector, limit=2000))
    except Exception:
        return 0


def _count_in_page(page: Any, selector: str) -> int:
    if not selector:
        return 0
    try:
        return int(
            page.evaluate(
                "(sel) => document.querySelectorAll(sel).length", selector
            )
            or 0
        )
    except Exception:
        return 0


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        pass
    try:
        module = __import__(name)
        return str(getattr(module, "__version__", "?"))
    except Exception:
        return "not-installed"


def _env() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "curl_cffi": _package_version("curl_cffi"),
        "patchright": _package_version("patchright"),
    }


# ── HTTP ────────────────────────────────────────────
def run_http(site: Site, *, timeout: int) -> BenchResult:
    # 惰性导入：没有 curl_cffi 时仍可 `ack-bench --help`
    from anti_crawl_kit.http import AntiRiskHttpClient
    from anti_crawl_kit.risk import RiskKind

    client = AntiRiskHttpClient("bench", timeout=timeout, max_retries=1)
    started = time.perf_counter()
    try:
        result = client.get_text(site.url)
        elapsed = int((time.perf_counter() - started) * 1000)
    except Exception as exc:
        return BenchResult(
            site=site.name,
            tool="http",
            ok=False,
            status=0,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            bytes=0,
            items=0,
            risk="exception",
            error=str(exc)[:200],
        )

    size = len(result.text or "")
    detection = detect_waf(result.text)
    ok = (
        result.status_code == 200
        and size >= site.min_bytes
        and result.risk.kind == RiskKind.OK
    )
    return BenchResult(
        site=site.name,
        tool="http",
        ok=ok,
        status=result.status_code,
        elapsed_ms=elapsed,
        bytes=size,
        items=_count_in_html(result.text, site.selector),
        risk=str(result.risk.kind),
        detection=detection,
    )


# ── 浏览器 ──────────────────────────────────────────
def run_browser_sites(
    sites: list[Site],
    *,
    headless: bool,
    timeout_ms: int,
    repeat: int,
) -> tuple[int, list[BenchResult]]:
    from anti_crawl_kit.session import BrowserSession

    results: list[BenchResult] = []
    started = time.perf_counter()
    with BrowserSession(
        headless=headless,
        anti_crawl=False,
        block_resources=False,
    ) as page:
        startup_ms = int((time.perf_counter() - started) * 1000)

        for site in sites:
            for _ in range(repeat):
                item_started = time.perf_counter()
                try:
                    response = page.goto(site.url, timeout=timeout_ms)
                    elapsed = int((time.perf_counter() - item_started) * 1000)
                    html = page.content()
                    title = page.title()
                    status = int(getattr(response, "status", 0) or 0)
                    captcha = detect_captcha_page(page)
                    size = len(html or "")
                    ok = (
                        status == 200
                        and size >= site.min_bytes
                        and not captcha.detected
                    )
                    results.append(
                        BenchResult(
                            site=site.name,
                            tool="browser",
                            ok=ok,
                            status=status,
                            elapsed_ms=elapsed,
                            bytes=size,
                            items=_count_in_page(page, site.selector),
                            risk="captcha" if captcha.detected else "ok",
                            detection=detect_waf(html),
                            title=title[:80],
                        )
                    )
                except Exception as exc:
                    results.append(
                        BenchResult(
                            site=site.name,
                            tool="browser",
                            ok=False,
                            status=0,
                            elapsed_ms=int(
                                (time.perf_counter() - item_started) * 1000
                            ),
                            bytes=0,
                            items=0,
                            risk="exception",
                            error=str(exc)[:200],
                        )
                    )
    return startup_ms, results


# ── 报告 ────────────────────────────────────────────
def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return int((ordered[mid - 1] + ordered[mid]) / 2)


def _aggregate(results: list[BenchResult]) -> dict[tuple[str, str], BenchResult]:
    """同一 (site, tool) 多次运行取中位数，成功优先。"""
    grouped: dict[tuple[str, str], list[BenchResult]] = {}
    for item in results:
        grouped.setdefault((item.site, item.tool), []).append(item)

    out: dict[tuple[str, str], BenchResult] = {}
    for key, runs in grouped.items():
        successful = [r for r in runs if r.ok] or runs
        base = dict(asdict(successful[0]))
        base["ok"] = any(r.ok for r in runs)
        base["elapsed_ms"] = _median([r.elapsed_ms for r in successful])
        base["bytes"] = _median([r.bytes for r in successful])
        base["items"] = _median([r.items for r in successful])
        out[key] = BenchResult(**base)
    return out


def _markdown(report: BenchReport, sites: list[Site]) -> str:
    agg = _aggregate(report.results)
    lines = [
        "# 最小 benchmark：curl_cffi vs patchright",
        "",
        f"- 时间：{report.started_at}",
        f"- 环境：Python {report.env['python']} · {report.env['platform']}",
        f"- 依赖：curl_cffi {report.env['curl_cffi']} · patchright {report.env['patchright']}",
        f"- 浏览器启动耗时：{report.startup_ms} ms（不计入单站耗时）",
        "",
        "| 站点 | 工具 | 成功 | 状态 | 耗时(ms) | 大小(KB) | 卡片数 | 风险/检测 |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for site in sites:
        for tool in ("http", "browser"):
            item = agg.get((site.name, tool))
            if item is None:
                continue
            note = item.risk
            if item.detection:
                note += f"/{item.detection}"
            if item.error:
                note += f"/{item.error[:40]}"
            lines.append(
                f"| {site.name} | {tool} | {'✅' if item.ok else '❌'} | "
                f"{item.status or '-'} | {item.elapsed_ms} | "
                f"{item.bytes / 1024:.1f} | {item.items} | {note} |"
            )

    lines.extend(["", "## 结论", ""])
    for site in sites:
        http = agg.get((site.name, "http"))
        browser = agg.get((site.name, "browser"))
        if http and browser and http.ok and browser.ok:
            speedup = browser.elapsed_ms / max(http.elapsed_ms, 1)
            if speedup >= 1:
                compare = f"HTTP 快 {speedup:.1f}×"
            else:
                compare = f"浏览器快 {1 / speedup:.1f}×"
            lines.append(
                f"- **{site.name}**：两条路都通，{compare}"
                f"（HTTP {http.elapsed_ms}ms vs 浏览器 {browser.elapsed_ms}ms）。"
                f"单页成本接近，但浏览器每次任务还要付启动成本"
                f"（本次 {report.startup_ms}ms）→ 批量优先 HTTP。"
            )
        elif http and not http.ok and browser and browser.ok:
            suffix = f"/{http.detection}" if http.detection else ""
            lines.append(
                f"- **{site.name}**：HTTP 被拦（{http.risk}{suffix}），"
                f"浏览器可用 → 该站必须走浏览器。"
            )
        elif http and http.ok and browser and not browser.ok:
            lines.append(
                f"- **{site.name}**：HTTP 可用，浏览器反而失败"
                f"（{browser.risk}）→ 不要盲目上浏览器。"
            )
        elif http and browser:
            lines.append(
                f"- **{site.name}**：两条路都失败（HTTP={http.risk}, "
                f"浏览器={browser.risk}）→ 先人工确认站点是否可访问。"
            )

    lines.extend(
        [
            "",
            "> 说明：单次运行受网络波动影响；要下结论请用 `--repeat 3` 取中位数。",
            "",
        ]
    )
    return "\n".join(lines)


def _print_table(report: BenchReport, sites: list[Site]) -> None:
    agg = _aggregate(report.results)
    print(f"{'站点':<10}{'工具':<10}{'成功':<6}{'状态':<6}{'耗时ms':<9}{'KB':<9}{'卡片':<6}风险")
    for site in sites:
        for tool in ("http", "browser"):
            item = agg.get((site.name, tool))
            if item is None:
                continue
            note = item.risk + (f"/{item.detection}" if item.detection else "")
            print(
                f"{site.name:<10}{tool:<10}{'OK' if item.ok else 'FAIL':<6}"
                f"{item.status or '-':<6}{item.elapsed_ms:<9}"
                f"{item.bytes / 1024:<9.1f}{item.items:<6}{note}"
            )


# ── 基线断言 ────────────────────────────────────────
def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _group_stats(results: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, float]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in results:
        groups.setdefault((str(row["site"]), str(row["tool"])), []).append(row)
    stats: dict[tuple[str, str], dict[str, float]] = {}
    for key, rows in groups.items():
        stats[key] = {
            "ok_rate": sum(1 for row in rows if row.get("ok")) / len(rows),
            "median_ms": _median([float(row.get("elapsed_ms", 0)) for row in rows]),
        }
    return stats


def _assert_baseline(
    report: BenchReport,
    baseline_path: str,
    tolerance: float,
) -> int:
    """成功率下降或中位耗时超基线 tolerance 时返回 1。"""
    baseline_file = Path(baseline_path)
    if not baseline_file.exists():
        print(f"[FAIL] 基线文件不存在: {baseline_file}")
        return 1
    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    base_stats = _group_stats(baseline.get("results", []))
    new_stats = _group_stats([asdict(item) for item in report.results])

    failures: list[str] = []
    for key, base in sorted(base_stats.items()):
        site, tool = key
        if key not in new_stats:
            failures.append(f"{site}/{tool}: 新结果缺少该组合")
            continue
        new = new_stats[key]
        if new["ok_rate"] < base["ok_rate"]:
            failures.append(
                f"{site}/{tool}: 成功率 {new['ok_rate']:.0%} < 基线 {base['ok_rate']:.0%}"
            )
        limit = base["median_ms"] * (1 + tolerance)
        if base["median_ms"] and new["median_ms"] > limit:
            failures.append(
                f"{site}/{tool}: 中位耗时 {new['median_ms']:.0f}ms > 基线 "
                f"{base['median_ms']:.0f}ms × {1 + tolerance:.2f}"
            )

    if failures:
        print("[FAIL] 基线断言未通过：")
        for line in failures:
            print(f"  - {line}")
        return 1
    print(f"[OK] 基线断言通过（{len(base_stats)} 个 site/tool 组合）")
    return 0


# ── 入口 ────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="curl_cffi vs patchright 最小 benchmark")
    parser.add_argument("--only", choices=("http", "browser", "both"), default="both")
    parser.add_argument("--repeat", type=int, default=1, help="每站重复次数")
    parser.add_argument("--timeout", type=int, default=30, help="单次超时（秒）")
    parser.add_argument("--headed", action="store_true", help="有头浏览器")
    parser.add_argument("--out", default="", help="输出目录")
    parser.add_argument(
        "--assert-baseline",
        default="",
        help="基线 results.json；成功率下降或耗时超阈值时退出码 1",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.2,
        help="中位耗时容忍度（默认 0.2 = 20%%）",
    )
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    sites = list(DEFAULT_SITES)
    report = BenchReport(
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        env=_env(),
    )

    if args.only in ("http", "both"):
        print("[bench] HTTP 直连 ...")
        for site in sites:
            for _ in range(max(args.repeat, 1)):
                result = run_http(site, timeout=args.timeout)
                report.results.append(result)
                print(
                    f"  {site.name}: {'OK' if result.ok else 'FAIL'} "
                    f"{result.status} {result.elapsed_ms}ms {result.risk}"
                )

    if args.only in ("browser", "both"):
        print("[bench] patchright ...")
        startup_ms, browser_results = run_browser_sites(
            sites,
            headless=not args.headed,
            timeout_ms=args.timeout * 1000,
            repeat=max(args.repeat, 1),
        )
        report.startup_ms = startup_ms
        report.results.extend(browser_results)
        print(f"  浏览器启动: {startup_ms}ms")

    out_dir = Path(args.out) if args.out else (
        ROOT / "benchmarks" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(
            {
                "started_at": report.started_at,
                "env": report.env,
                "startup_ms": report.startup_ms,
                "results": [asdict(r) for r in report.results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_markdown(report, sites), encoding="utf-8")

    print()
    _print_table(report, sites)
    print(f"\n报告: {out_dir / 'report.md'}")

    if args.assert_baseline:
        return _assert_baseline(report, args.assert_baseline, args.tolerance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
