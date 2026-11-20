#!/usr/bin/env python3
"""``ack-doctor`` —— 一条命令自检环境，输出可直接复制的修复建议。

为什么需要它：README 里的「如果……请……」对第一次上手的人等于没有。
这里把常见坑逐条检查，能修的直接给命令，不能修的明确说「不影响」。

用法::

    ack doctor
    ack doctor --port 8899 --runs C:\\tmp\\runs
    ack doctor --json

退出码：
    0  没有 FAIL（WARN 代表可选能力缺失，不影响纯逻辑）
    1  有 FAIL（Python 版本太低、核心包不可导入、运行目录不可写）
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sys
import tempfile
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

_LEVEL_ORDER = {"OK": 0, "INFO": 1, "WARN": 2, "FAIL": 3}

# 可选依赖：模块名 → (dist 名, extra, 缺了影响什么)
_OPTIONAL = {
    "yaml": ("PyYAML", "config", "recipe / ack-ui / 配置读取"),
    "curl_cffi": ("curl_cffi", "http", "HTTP 直连（TLS 指纹）"),
    "patchright": ("patchright", "browser", "浏览器步骤（browser_*）"),
    "mcp": ("mcp", "mcp", "MCP server（ack-mcp）"),
}


@dataclass
class Check:
    level: str                 # OK / INFO / WARN / FAIL
    name: str
    detail: str = ""
    fix: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "level": self.level,
            "name": self.name,
            "detail": self.detail,
            "fix": self.fix,
        }
        if self.extra:
            data.update(self.extra)
        return data


def _module_version(name: str, dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        pass
    module = sys.modules.get(name)
    return getattr(module, "__version__", None) if module else None


def _check_python() -> Check:
    current = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 10):
        return Check("OK", "Python", f"{current} (>= 3.10)")
    return Check(
        "FAIL",
        "Python 版本过低",
        f"{current}，需要 >= 3.10",
        "升级 Python：https://www.python.org/downloads/",
    )


def _check_core() -> Check:
    try:
        from anti_crawl_kit import __version__

        return Check("OK", "anti-crawl-kit", __version__)
    except Exception as exc:  # pragma: no cover - 安装坏了才会走到
        return Check(
            "FAIL",
            "核心包不可导入",
            f"{type(exc).__name__}: {exc}",
            'pip install -e ".[dev]"',
        )


def _check_optional() -> list[Check]:
    checks = []
    for module, (dist, extra, purpose) in _OPTIONAL.items():
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if found:
            version = _module_version(module, dist)
            checks.append(
                Check(
                    "OK",
                    f"{dist}",
                    f"{version or '已安装'} · {purpose}",
                    extra={"module": module, "installed": True},
                )
            )
        else:
            checks.append(
                Check(
                    "WARN",
                    f"{dist} 未安装",
                    f"{purpose} 不可用",
                    f'pip install "anti-crawl-kit[{extra}]"',
                    {"module": module, "installed": False},
                )
            )
    return checks


def _check_browsers() -> list[Check]:
    checks: list[Check] = []
    try:
        from ..session import (
            detect_chrome_version,
            detect_edge_version,
            detect_patchright_chromium,
        )
    except Exception as exc:  # pragma: no cover - 只可能是包结构损坏
        return [
            Check(
                "FAIL",
                "浏览器探测模块不可用",
                f"{type(exc).__name__}: {exc}",
                "重新安装：pip install --force-reinstall anti-crawl-kit",
            )
        ]

    chrome = detect_chrome_version()
    edge = detect_edge_version()
    if chrome:
        checks.append(Check("OK", "Chrome", chrome))
    if edge:
        checks.append(Check("OK", "Edge", edge))
    if not chrome and not edge:
        checks.append(
            Check(
                "WARN",
                "没有找到 Chrome / Edge",
                "browser_* 步骤不可用，纯逻辑和 HTTP 不受影响",
                "安装 Chrome 或 Edge；也可以用 patchright 自带 Chromium",
            )
        )
        return checks

    expected = detect_patchright_chromium()
    actual = chrome or edge
    if not expected:
        checks.append(
            Check(
                "INFO",
                "patchright 期望版本未知",
                "跳过 Chrome 大版本匹配检查（不影响使用）",
            )
        )
        return checks
    try:
        gap = abs(int(actual.split(".")[0]) - int(expected.split(".")[0]))
    except (ValueError, IndexError):
        return checks
    if gap > 1:
        checks.append(
            Check(
                "WARN",
                "Chrome 与 patchright 版本差得较多",
                f"系统 {actual}，patchright 期望 {expected}（差 {gap} 个大版本）",
                "浏览器步骤异常时再处理：升级 patchright，或改用 Edge（--channel msedge）",
            )
        )
    else:
        checks.append(
            Check(
                "OK",
                "Chrome / patchright 版本匹配",
                f"系统 {actual}，期望 {expected}",
            )
        )
    return checks


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _check_port(port: int) -> Check:
    if _port_free(port):
        return Check("OK", f"端口 {port}", "空闲（ack-ui 默认端口）")
    return Check(
        "WARN",
        f"端口 {port} 已被占用",
        "ack-ui 无法用默认端口启动",
        f"ack-ui --port {port + 1}",
    )


def _check_runs_dir(path: Path) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=path, prefix=".ack-doctor-", delete=True
        ):
            pass
    except OSError as exc:
        return Check(
            "FAIL",
            "运行目录不可写",
            f"{path} · {exc}",
            "换一个可写目录：ack-ui --runs <目录>",
        )
    return Check("OK", "运行目录可写", str(path.resolve()))


def _check_proxy() -> Check:
    """只在真的设置了代理地址时告警。

    ``NO_PROXY`` 是排除列表，单独存在不代表走代理，不能当告警。
    """
    proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    found = {key: os.environ[key] for key in proxy_keys if os.environ.get(key)}
    if not found:
        return Check("OK", "代理环境变量", "未设置")
    detail = "，".join(f"{key}={value}" for key, value in found.items())
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    if no_proxy:
        detail += f"；NO_PROXY={no_proxy}"
    return Check(
        "WARN",
        "检测到代理环境变量",
        f"{detail} · 确认目标站是否该走代理",
        "不需要时清空：set HTTPS_PROXY=（Windows） / unset HTTPS_PROXY（bash）",
    )


def _check_tmpdir() -> Check:
    try:
        with tempfile.NamedTemporaryFile(prefix="ack-doctor-", delete=True):
            pass
        return Check("OK", "临时目录可写", tempfile.gettempdir())
    except OSError as exc:
        return Check(
            "FAIL",
            "临时目录不可写",
            f"{tempfile.gettempdir()} · {exc}",
            "检查 TMP / TEMP 环境变量",
        )


def _collect(port: int, runs: Path) -> list[Check]:
    checks = [_check_python(), _check_core()]
    checks.extend(_check_optional())
    checks.extend(_check_browsers())
    checks.append(_check_port(port))
    checks.append(_check_runs_dir(runs))
    checks.append(_check_tmpdir())
    checks.append(_check_proxy())
    return checks


def _print_checks(checks: list[Check]) -> None:
    for check in checks:
        line = f"[{check.level}]".ljust(7) + f" {check.name}"
        # 短说明直接跟在标题后面，长说明和修复建议另起一行，避免输出太散
        inline = bool(check.detail) and not check.fix and len(check.detail) <= 60
        if inline:
            line += f" {check.detail}"
        print(line)
        if check.detail and not inline:
            print(f"        {check.detail}")
        if check.fix:
            print(f"        → {check.fix}")


def _next_steps(checks: list[Check]) -> list[str]:
    installed = {
        check.extra.get("module"): check.extra.get("installed")
        for check in checks
        if check.extra.get("module")
    }
    steps = []
    if not installed.get("yaml"):
        steps.append('pip install "anti-crawl-kit[config]"')
    steps.append("ack init recipe")
    steps.append("ack-recipe my_recipe.yaml")
    if not installed.get("patchright"):
        steps.append('需要浏览器时：pip install "anti-crawl-kit[browser]"')
    return steps


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ack doctor",
        description="自检运行环境：Python、可选依赖、浏览器、端口、目录、代理",
    )
    parser.add_argument("--port", type=int, default=8766, help="检查的端口（默认 8766）")
    parser.add_argument("--runs", default="runs", help="检查的运行目录（默认 runs）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    checks = _collect(args.port, Path(args.runs))

    failures = sum(1 for check in checks if check.level == "FAIL")
    warnings = sum(1 for check in checks if check.level == "WARN")

    if args.json:
        print(
            json.dumps(
                {
                    "python": ".".join(str(part) for part in sys.version_info[:3]),
                    "checks": [check.to_dict() for check in checks],
                    "failures": failures,
                    "warnings": warnings,
                    "ok": failures == 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if failures else 0

    _print_checks(checks)
    print()
    if failures:
        print(f"[FAIL] {failures} 项必须修复，{warnings} 项建议关注")
    elif warnings:
        print(f"[WARN] 没有阻塞问题，{warnings} 项可选能力缺失")
    else:
        print("[OK] 环境正常")

    steps = _next_steps(checks)
    if steps:
        print("[INFO] 下一步:")
        for step in steps:
            print(f"       {step}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
