#!/usr/bin/env python3
"""``ack`` —— 统一入口，把 ``ack-xxx`` 收成子命令。

两种写法等价，旧的 ``ack-xxx`` 全部保留：

    ack doctor          ==  ack-doctor
    ack init recipe     ==  ack-init recipe
    ack recipe x.yaml   ==  ack-recipe x.yaml
    ack report --dir runs

这样做只是少记几个命令名，不改变任何行为。
"""
from __future__ import annotations

import importlib
import sys

# 子命令 → (模块, 函数)
COMMANDS: dict[str, tuple[str, str]] = {
    "doctor": ("anti_crawl_kit.tools.doctor", "main"),
    "init": ("anti_crawl_kit.tools.init_cli", "main"),
    "probe": ("anti_crawl_kit.tools.agent_probe", "main"),
    "hunt": ("anti_crawl_kit.tools.js_api_hunt", "main"),
    "dom": ("anti_crawl_kit.tools.dom_probe", "main"),
    "detect": ("anti_crawl_kit.tools.detector_check", "main"),
    "bench": ("anti_crawl_kit.tools.bench", "main"),
    "audit": ("anti_crawl_kit.tools.path_audit", "main"),
    "health": ("anti_crawl_kit.tools.health_cli", "main"),
    "smoke": ("anti_crawl_kit.tools.smoke_cli", "main"),
    "assess": ("anti_crawl_kit.tools.assess_cli", "main"),
    "report": ("anti_crawl_kit.tools.report_cli", "main"),
    "recipe": ("anti_crawl_kit.tools.recipe_cli", "main"),
    "mcp": ("anti_crawl_kit.mcp_server", "main"),
    "ui": ("anti_crawl_kit.ui", "main"),
}

_HELP = """\
用法: ack <命令> [参数...]

常用：
  ack doctor                 自检环境，输出修复建议
  ack init recipe            生成能直接跑的配方骨架
  ack init assess            从 DOM 探测结果生成评估配置
  ack recipe <file>          跑配方并落盘
  ack report --dir runs      报告转 HTML + 索引
  ack ui                     本地控制台（http://127.0.0.1:8766）

全部命令：
  doctor  init  probe  hunt  dom  detect  bench  audit
  health  smoke  assess  report  recipe  mcp  ui

等价写法：ack recipe == ack-recipe，参数完全一致。
"""


def _print_help() -> None:
    print(_HELP, end="")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        _print_help()
        return 0
    if argv[0] in ("-V", "--version"):
        from anti_crawl_kit import __version__

        print(__version__)
        return 0

    name, rest = argv[0], argv[1:]
    target = COMMANDS.get(name)
    if target is None:
        print(f"[FAIL] 未知命令: {name}", file=sys.stderr)
        _print_help()
        return 2

    module = importlib.import_module(target[0])
    return getattr(module, target[1])(rest)


if __name__ == "__main__":
    raise SystemExit(main())
