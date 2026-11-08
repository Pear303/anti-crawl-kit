#!/usr/bin/env python3
"""``ack-smoke`` —— 真实站点冒烟（发行门槛）。

用法::

    ack-smoke                              # 全部用例（含浏览器）
    ack-smoke --no-browser                 # 只跑纯 HTTP，浏览器用例 SKIP
    ack-smoke --only books_pagination,quotes_js_render
    ack-smoke --headed                     # 有头（排查登录/滚动用）
    ack-smoke --json smoke.json
    ack-smoke --strict                     # SKIP 也视为失败（CI 发行门槛）
    ack-smoke --repeat 3                   # 每类跑 3 次，全部通过才算过

退出码：
    0  无 FAIL
    1  存在 FAIL（strict 下 SKIP 也算）
    2  环境/配置错误（缺 curl_cffi、目标站点全不可达等）
"""
from __future__ import annotations

import argparse
import sys
from contextlib import ExitStack
from pathlib import Path

from anti_crawl_kit.smoke import (
    DEFAULT_CASES,
    SmokeCase,
    SmokeReport,
    SmokeResult,
    run_smoke,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ack-smoke",
        description="真实站点冒烟：8 类链路逐条验证，负向控制内置",
    )
    parser.add_argument("--only", type=str, default="", help="只跑指定用例（逗号分隔）")
    parser.add_argument("--no-browser", action="store_true", help="只跑纯 HTTP 用例")
    parser.add_argument("--headed", action="store_true", help="有头浏览器")
    parser.add_argument("--json", type=str, default="", help="落盘 JSON 报告")
    parser.add_argument("--strict", action="store_true", help="SKIP 也返回退出码 1")
    parser.add_argument("--repeat", type=int, default=1, help="每个用例重复次数（默认 1）")
    return parser


def _merge_attempts(attempts: list[SmokeReport]) -> SmokeReport:
    """多次运行合并：任一 FAIL 即 FAIL；否则 PASS 优先，最后 SKIP。"""
    if not attempts:
        return SmokeReport()
    merged = SmokeReport(
        started_at=attempts[0].started_at,
        finished_at=attempts[-1].finished_at,
    )
    names: list[str] = []
    for report in attempts:
        for result in report.results:
            if result.name not in names:
                names.append(result.name)

    for name in names:
        rows = [
            result
            for report in attempts
            for result in report.results
            if result.name == name
        ]
        failed = [row for row in rows if row.status == "FAIL"]
        passed = [row for row in rows if row.status == "PASS"]
        base = (failed or passed or rows)[0]
        metrics = dict(base.metrics)
        metrics["attempts"] = [
            {"ok": row.ok, "status": row.status, "elapsed_ms": row.elapsed_ms}
            for row in rows
        ]
        merged.add(
            SmokeResult(
                name=base.name,
                kind=base.kind,
                ok=bool(passed) and not failed,
                status="FAIL" if failed else ("PASS" if passed else "SKIP"),
                expected=base.expected,
                actual=base.actual,
                http_count=base.http_count,
                browser_count=base.browser_count,
                elapsed_ms=max(row.elapsed_ms for row in rows),
                detail=base.detail,
                error="; ".join(sorted({row.error for row in rows if row.error})),
                metrics=metrics,
            )
        )
    return merged


def _select_cases(only: str) -> list[SmokeCase]:
    if not only:
        return list(DEFAULT_CASES)
    wanted = {item.strip() for item in only.split(",") if item.strip()}
    return [case for case in DEFAULT_CASES if case.name in wanted]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    cases = _select_cases(args.only)
    if not cases:
        print(f"[FAIL] --only 没有匹配到用例: {args.only}", file=sys.stderr)
        return 2

    try:
        from anti_crawl_kit.http import AntiRiskHttpClient
    except ModuleNotFoundError:
        print(
            "[FAIL] 需要 curl_cffi：pip install 'anti-crawl-kit[http]'",
            file=sys.stderr,
        )
        return 2

    client = AntiRiskHttpClient("smoke", timeout=25, max_retries=2)
    repeat = max(1, args.repeat)

    with ExitStack() as stack:
        page = None
        if not args.no_browser:
            try:
                from anti_crawl_kit.session import BrowserSession

                page = stack.enter_context(
                    BrowserSession(headless=not args.headed, skip_version_check=True)
                )
            except Exception as exc:  # 浏览器不可用 → 浏览器用例 SKIP，不阻塞 HTTP 用例
                print(
                    f"[WARN] 浏览器不可用，浏览器用例将 SKIP: "
                    f"{type(exc).__name__}: {str(exc)[:160]}",
                    file=sys.stderr,
                )
                page = None

        attempts = [
            run_smoke(cases, page=page, http_client=client) for _ in range(repeat)
        ]

    report = attempts[0] if len(attempts) == 1 else _merge_attempts(attempts)
    print(report.render())
    if args.json:
        path = report.write(args.json)
        print(f"[INFO] JSON 报告: {path}")

    if report.has_fail():
        return 1
    if args.strict and report.counts().get("SKIP", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
