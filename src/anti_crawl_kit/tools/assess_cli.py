#!/usr/bin/env python3
"""``ack-assess`` —— G0~G4 技术验证评估（Go / No-Go）。

用法::

    ack-assess --config examples/assessments/books.yaml
    ack-assess --config ... --out runs/assessment-books
    ack-assess --config ... --no-audit        # 跳过 G1 路径审计

退出码：
    0  go / conditional_go
    1  no_go
    2  配置错误
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from anti_crawl_kit.assess import assess, load_assessment_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ack-assess",
        description="G0~G4 技术验证：形态 / 路径 / 数据 / 稳定 / 工程",
    )
    parser.add_argument("--config", required=True, help="评估配置 YAML/JSON")
    parser.add_argument("--out", default="", help="输出目录（默认 runs/assessment-<platform>）")
    parser.add_argument("--no-audit", action="store_true", help="跳过 G1 路径审计")
    parser.add_argument(
        "--channel",
        default="",
        choices=["", "chrome", "msedge"],
        help="G1 审计浏览器通道（默认自动：chrome → msedge）",
    )
    args = parser.parse_args(argv)

    try:
        config = load_assessment_config(args.config)
    except Exception as exc:
        print(f"[FAIL] 配置读取失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if not config.platform or not config.url:
        print("[FAIL] 配置必须包含 platform 和 url", file=sys.stderr)
        return 2
    if args.no_audit:
        config = replace(config, audit_config="")
    if args.channel:
        config = replace(config, channel=args.channel)

    out_dir = Path(args.out) if args.out else Path("runs") / f"assessment-{config.platform}"
    try:
        report = assess(config, out_dir=out_dir)
    except Exception as exc:
        print(f"[FAIL] 评估失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(report.render())
    json_path, md_path = report.write(out_dir)
    print(f"\n[INFO] JSON: {json_path}")
    print(f"[INFO] Markdown: {md_path}")
    return 0 if report.decision in ("go", "conditional_go") else 1


if __name__ == "__main__":
    raise SystemExit(main())
