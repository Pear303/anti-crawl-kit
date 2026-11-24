#!/usr/bin/env python3
"""``ack-init`` —— 生成能直接跑的配方骨架。

原则：生成的东西必须先能跑，再谈改。所以默认模板是纯逻辑的，
不需要浏览器、网络或额外依赖；http / browser 模板会明确写出前置条件。

用法::

    ack init recipe                       # 生成 my_recipe.yaml（纯逻辑）
    ack init recipe --kind http           # 生成 HTTP 采集骨架
    ack init recipe --kind browser        # 生成浏览器采集骨架
    ack init recipe --out jobs/daily.yaml
    ack init health                       # 等价于 ack-health --init

退出码：
    0  生成成功，或文件已存在（不覆盖）
    2  参数错误 / 写入失败
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

LOGIC_TEMPLATE = """\
# 由 `ack init recipe` 生成。
# 运行：ack-recipe my_recipe.yaml
# 传参：ack-recipe my_recipe.yaml --inputs name=alice
# 看报告：运行后打开 runs/my_recipe/latest.html
id: my_recipe
title: 我的第一个配方
description: 纯逻辑示例：不联网、不启动浏览器，先确认整条链路能跑通
version: "1"

inputs:
  name:
    type: string
    default: world

steps:
  - id: greet
    uses: set_var
    with:
      name: greeting
      value: "hello ${inputs.name}"

  - id: note
    uses: log
    with:
      message: "配方已执行，问候语：${steps.greet.value}"

  # assert 失败会让配方变成 failed，并在报告里标红。
  - id: check
    uses: assert
    with:
      value: "${steps.greet.value}"
      op: contains
      expected: "hello"

outputs:
  greeting: "${steps.greet.value}"
"""

HTTP_TEMPLATE = """\
# 由 `ack init recipe --kind http` 生成。
# 前置：pip install "anti-crawl-kit[http]"
# 运行：ack-recipe my_http_recipe.yaml
#
# allow_hosts 是硬边界：不在列表里的域名会直接报错，避免配方被改成任意目标。
id: my_http_recipe
title: HTTP 采集骨架
description: 先拿 HTML，再做断言；只采公开数据
version: "1"
allow_hosts:
  - quotes.toscrape.com

inputs:
  url:
    type: string
    default: "https://quotes.toscrape.com/"

steps:
  - id: fetch
    uses: http_get
    with:
      url: "${inputs.url}"

  - id: check_status
    uses: assert
    with:
      value: "${steps.fetch.status}"
      op: eq
      expected: 200

  - id: check_body
    uses: assert
    with:
      value: "${steps.fetch.text}"
      op: contains
      expected: "quotes"

outputs:
  status: "${steps.fetch.status}"
"""

BROWSER_TEMPLATE = """\
# 由 `ack init recipe --kind browser` 生成。
# 前置：pip install "anti-crawl-kit[browser]" 且本机有 Chrome 或 Edge
# 运行：ack-recipe my_browser_recipe.yaml --headed
#
# 第一次会启动有头浏览器，方便看清页面；确认稳定后去掉 --headed。
id: my_browser_recipe
title: 浏览器采集骨架
description: 打开页面、数卡片数量；选择器换成你自己的
version: "1"
allow_hosts:
  - quotes.toscrape.com

inputs:
  url:
    type: string
    default: "https://quotes.toscrape.com/"

steps:
  - id: open
    uses: browser_goto
    with:
      url: "${inputs.url}"

  # 选择器失效时先跑 ack-dom --url <URL>，不要凭感觉改。
  - id: cards
    uses: browser_count
    with:
      selector: "div.quote"

  - id: check
    uses: assert
    with:
      value: "${steps.cards.count}"
      op: gte
      expected: 1

outputs:
  cards: "${steps.cards.count}"
"""

KINDS = {
    "logic": ("my_recipe.yaml", LOGIC_TEMPLATE),
    "http": ("my_http_recipe.yaml", HTTP_TEMPLATE),
    "browser": ("my_browser_recipe.yaml", BROWSER_TEMPLATE),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ack init",
        description="生成能直接运行的配置骨架",
    )
    sub = parser.add_subparsers(dest="kind", required=True)

    recipe = sub.add_parser("recipe", help="生成配方（recipe）")
    recipe.add_argument(
        "--type",
        dest="recipe_type",
        default="logic",
        choices=sorted(KINDS),
        help="模板类型：logic（默认，零依赖）/ http / browser",
    )
    recipe.add_argument("--out", default="", help="输出路径；默认按类型命名")
    recipe.add_argument("--force", action="store_true", help="覆盖已存在的文件")

    health = sub.add_parser("health", help="生成健康体检模板（等价 ack-health --init）")
    health.add_argument("--out", default="", help="输出路径；默认 ack_health.yaml")

    assess = sub.add_parser("assess", help="从 DOM 探测结果生成评估配置")
    assess.add_argument(
        "--from-probe", required=True, help="dom_probe / ack-dom --out 生成的 JSON"
    )
    assess.add_argument("--out-dir", default="", help="输出目录，默认当前目录")
    assess.add_argument("--platform", default="", help="平台名，默认从域名推断")
    assess.add_argument("--item-selector", default="", help="覆盖列表容器选择器")
    assess.add_argument("--fields", default="", help="逗号分隔，只保留这些字段名")
    assess.add_argument("--runs", type=int, default=3, help="G3 重复次数")
    assess.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    return parser


def _init_recipe(args: argparse.Namespace) -> int:
    default_name, template = KINDS[args.recipe_type]
    target = Path(args.out or default_name)
    if target.exists() and not args.force:
        print(f"[WARN] 文件已存在，未覆盖: {target}")
        print("       要覆盖请加 --force")
        return 0

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template, encoding="utf-8")
    except OSError as exc:
        print(f"[FAIL] 写入失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"[OK] 已生成配方: {target}")
    try:
        from anti_crawl_kit.recipe import load_recipe

        recipe = load_recipe(target)
    except ModuleNotFoundError:
        print('[WARN] 未安装 PyYAML，先装：pip install "anti-crawl-kit[config]"')
        print("[INFO] 然后再运行：ack-recipe " + str(target))
        return 0
    except Exception as exc:
        print(f"[FAIL] 生成的配方无法解析: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"[INFO] 配方 id={recipe.id}，{len(recipe.steps)} 步")
    print(f"[INFO] 运行：ack-recipe {target}")
    if args.recipe_type != "logic":
        print("[WARN] 这个模板需要网络/浏览器，先确认依赖已安装")
    else:
        print("[INFO] 纯逻辑模板，不需要浏览器和网络")
    return 0


def _init_assess(args: argparse.Namespace) -> int:
    import json

    from anti_crawl_kit.assess_draft import draft_assessment, write_draft

    probe_path = Path(args.from_probe)
    try:
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"[FAIL] 读不到探测结果: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"[FAIL] 探测结果不是合法 JSON: {exc}", file=sys.stderr)
        return 2

    fields = [item.strip() for item in (args.fields or "").split(",") if item.strip()]
    try:
        result = draft_assessment(
            probe,
            platform=args.platform,
            item_selector=args.item_selector,
            fields=fields or None,
            runs=args.runs,
        )
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir or ".")
    target = out_dir / f"{result['platform']}_assessment.yaml"
    if target.exists() and not args.force:
        print(f"[WARN] 文件已存在，未覆盖: {target}")
        print("       要覆盖请加 --force")
        return 0

    try:
        paths = write_draft(result, out_dir)
    except OSError as exc:
        print(f"[FAIL] 写入失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"[OK] 配置: {paths['config_path']}")
    if paths.get("audit_path"):
        print(f"[OK] 审计: {paths['audit_path']}")
    names = ",".join(f["name"] for f in result["fields"]) or "（无）"
    print(f"[INFO] item_selector={result['item_selector'] or '（未识别）'}")
    print(f"[INFO] 字段: {names}")
    for warning in result["warnings"]:
        print(f"[WARN] {warning}")
    print(f"[INFO] 下一步：ack-assess --config \"{paths['config_path']}\"")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.kind == "recipe":
        return _init_recipe(args)
    if args.kind == "assess":
        return _init_assess(args)

    from .health_cli import main as health_main

    health_args = ["--init"]
    if getattr(args, "out", ""):
        health_args += ["--out", args.out]
    return health_main(health_args)


if __name__ == "__main__":
    raise SystemExit(main())
