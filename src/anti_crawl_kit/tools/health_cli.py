#!/usr/bin/env python3
"""``ack-health`` —— 配置驱动的平台健康体检 CLI。

用法::

    ack-health --config ack_health.yaml              # 静态体检（零浏览器）
    ack-health --config ack_health.yaml --browser    # 静态 + 免登录动态探针
    ack-health --config ack_health.yaml --json out.json
    ack-health --init                                # 生成配置模板

退出码：
    0  无 FAIL（``--strict`` 时连 WARN 也不允许）
    1  存在 FAIL / strict 下存在 WARN
    2  配置错误（文件缺失、格式错误、依赖缺失）

红线：只读探测，不点击、不登录、不发布、不写平台数据。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from anti_crawl_kit.health import (
    HealthReport,
    HealthStatus,
    HealthTarget,
    load_health_config,
    probe_health_page,
    run_static_health_checks,
)

_DEFAULT_CONFIGS = ("ack_health.yaml", "ack_health.yml", "ack_health.json")

_TEMPLATE = """\
# ack-health 体检配置（复制后按平台填写）
# module_root 相对本文件；也可以用 --module-root 覆盖。
module_root: src

platforms:
  - platform: demo
    label: 示例平台
    # 静态检查：模块文件 + 类名必须逐字存在
    collector_module: demo.collectors.demo
    collector_class: DemoCollector
    # 登录判据：认证 cookie 名单必须出现在以下任一源码中
    login_module: demo.login
    cookie_module: demo.identity
    auth_cookies: [sessionid]
    entry_param: uid
    qrcode_selector: ".qrcode img"
    needs_login: true
    # 动态探针（--browser 时启用；只读，不登录）
    probe_url: https://example.com/profile/1
    probe_selectors: [".profile-card"]
    probe_state_names: ["window.__INITIAL_STATE__"]
"""


def _find_default_config() -> Path | None:
    for name in _DEFAULT_CONFIGS:
        path = Path(name)
        if path.is_file():
            return path
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ack-health",
        description="平台健康体检：静态注册表校验 + 免登录动态探针",
    )
    parser.add_argument("--config", type=str, default="", help="YAML/JSON 配置路径")
    parser.add_argument("--module-root", type=str, default="", help="覆盖配置里的 module_root")
    parser.add_argument("--only", type=str, default="", help="只体检指定平台（逗号分隔）")
    parser.add_argument("--browser", action="store_true", help="启用免登录动态探针")
    parser.add_argument("--json", type=str, default="", help="落盘 JSON 报告")
    parser.add_argument("--html", type=str, default="", help="落盘单文件 HTML 报告")
    parser.add_argument("--strict", action="store_true", help="WARN 也返回退出码 1")
    parser.add_argument("--init", action="store_true", help="生成配置模板 ack_health.yaml")
    parser.add_argument("--wait-ms", type=int, default=3000, help="动态探针等待渲染毫秒数")
    parser.add_argument("--timeout", type=int, default=30000, help="动态探针导航超时毫秒数")
    return parser


def _run_browser_probes(
    report: HealthReport,
    targets: list[HealthTarget],
    *,
    wait_ms: int,
    timeout: int,
) -> None:
    from anti_crawl_kit.session import BrowserSession

    with BrowserSession(headless=True, skip_version_check=True) as page:
        for target in targets:
            report.add(probe_health_page(page, target, wait_ms=wait_ms, timeout=timeout))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.init:
        path = Path("ack_health.yaml")
        if path.exists():
            print(f"[WARN] 配置已存在，未覆盖: {path}")
            return 0
        path.write_text(_TEMPLATE, encoding="utf-8")
        print(f"[OK] 已生成配置模板: {path}")
        print("[WARN] 这是 demo 模板，直接运行会 FAIL（模块/类名/cookie 名都还是示例值）。")
        print("[INFO] 先把 platforms 里的模块、类名、cookie 名、选择器改成你的项目，再运行:")
        print("       ack-health --config ack_health.yaml")
        return 0

    config_path = Path(args.config) if args.config else _find_default_config()
    if config_path is None or not config_path.is_file():
        print(
            "[FAIL] 找不到体检配置。用 --config 指定，或先运行 "
            "`ack-health --init` 生成模板。",
            file=sys.stderr,
        )
        return 2

    try:
        module_root, targets = load_health_config(config_path)
    except Exception as exc:
        print(f"[FAIL] 配置读取失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.module_root:
        module_root = Path(args.module_root).resolve()

    only = {item.strip() for item in args.only.split(",") if item.strip()}
    if only:
        targets = [target for target in targets if target.platform in only]
        if not targets:
            print(f"[FAIL] --only 没有匹配到平台: {sorted(only)}", file=sys.stderr)
            return 2

    report = run_static_health_checks(targets, module_root=module_root)

    if args.browser:
        try:
            _run_browser_probes(
                report, targets, wait_ms=args.wait_ms, timeout=args.timeout
            )
        except Exception as exc:
            report.add(
                _failed("probe", "-", f"动态探针编排异常: {type(exc).__name__}: {exc}")
            )
        report.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(report.render())
    if args.json:
        path = report.write(args.json)
        print(f"[INFO] JSON 报告: {path}")
    if args.html:
        from anti_crawl_kit.report import write_html

        path = write_html(report.to_dict(), args.html, kind="health")
        print(f"[INFO] HTML 报告: {path}")

    if report.has_fail():
        return 1
    if args.strict and report.has_warn():
        return 1
    return 0


def _failed(dimension: str, platform: str, detail: str):
    """小工具：构造 FAIL 结果（避免在异常分支重复导入）。"""
    from anti_crawl_kit.health import CheckResult

    return CheckResult(dimension, platform, HealthStatus.FAIL, detail)


if __name__ == "__main__":
    raise SystemExit(main())
