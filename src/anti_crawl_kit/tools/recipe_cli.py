#!/usr/bin/env python3
"""``ack-recipe`` —— 命令行跑配方，落盘并对比上一次。

``ack-ui`` 适合人看，CI / 定时任务更适合命令行：

    ack-recipe examples/recipes/local_demo.yaml
    ack-recipe examples/recipes/local_demo.yaml --inputs name=world --runs runs
    ack-recipe examples/recipes/local_demo.yaml --baseline auto

退出码：
    0  配方 done，且没有基线退化
    1  配方失败 / needs_agent / 基线对比发现退化
    2  参数错误 / 配方加载失败 / 落盘失败
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from anti_crawl_kit.console import redact_mapping
from anti_crawl_kit.recipe import load_recipe, run_recipe
from anti_crawl_kit.report import detect_kind, load_json
from anti_crawl_kit.report_diff import diff_reports
from anti_crawl_kit.runs import find_previous_report, persist_run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ack-recipe",
        description="命令行跑配方：落盘到 runs/<recipe>/<时间戳>.json|html，并可对比上一次",
    )
    parser.add_argument("recipe", help="配方文件（YAML / JSON）")
    parser.add_argument(
        "--inputs",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="输入参数，可重复；值会尝试按 JSON 解析，失败按字符串",
    )
    parser.add_argument("--runs", default="runs", help="报告目录（默认 runs）")
    parser.add_argument(
        "--baseline",
        default="",
        metavar="auto|JSON",
        help="基线：auto 自动找上一次 recipe 报告，或指定 JSON；有退化时退出码 1",
    )
    parser.add_argument("--headed", action="store_true", help="需要浏览器时用有头模式")
    parser.add_argument(
        "--channel",
        default="",
        choices=["", "chrome", "msedge"],
        help="浏览器通道（默认自动：chrome → msedge）",
    )
    return parser


def _parse_inputs(pairs: list[str]) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--inputs 需要 KEY=VALUE 形式: {pair}")
        key, raw = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--inputs 的 KEY 不能为空: {pair}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        inputs[key] = value
    return inputs


def _resolve_inputs(
    specs: Any,
    provided: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """把配方声明的 ``default`` 填进来，并检查必填项。

    ``run_recipe`` 只接收一个扁平 dict，不会读 ``inputs`` 声明；``ack-ui``
    是前端表单负责补默认值，命令行这边得自己补，否则 ``${inputs.x}`` 一用
    就 KeyError。

    Returns:
        (补全后的输入, 缺失的必填项名称列表)
    """
    if not isinstance(specs, dict):
        return dict(provided), []

    merged = dict(provided)
    missing: list[str] = []
    for name, spec in specs.items():
        if name in merged:
            continue
        if isinstance(spec, dict) and "default" in spec:
            merged[name] = spec["default"]
            continue
        if isinstance(spec, dict) and spec.get("required"):
            missing.append(str(name))
    return merged, missing


def _print_event(event: dict[str, Any]) -> None:
    kind = str(event.get("type") or "")
    step_id = str(event.get("step_id") or "")
    if kind == "step_start":
        print(f"[..] {step_id} {event.get('uses') or ''}".rstrip())
    elif kind == "step_ok":
        print(f"[OK] {step_id} ({event.get('elapsed_ms', 0)}ms)")
    elif kind == "step_skipped":
        print(f"[SKIP] {step_id}")
    elif kind == "step_failed":
        print(f"[FAIL] {step_id}: {event.get('error') or ''}", file=sys.stderr)
    elif kind == "needs_agent":
        print(f"[AGENT] {step_id}")


def _baseline_diff(
    current_path: Path,
    baseline_arg: str,
    result: dict[str, Any],
) -> tuple[int, str]:
    """返回 (退出码, 空字符串或错误说明)。退化时退出码 1。"""
    baseline_path: Path | None = None
    if baseline_arg == "auto":
        baseline_path = find_previous_report(current_path, "recipe")
        if baseline_path is None:
            print("[WARN] 没有找到上一次的 recipe 报告，本次不对比", file=sys.stderr)
            return 0, ""
    else:
        baseline_path = Path(baseline_arg)
        if not baseline_path.is_file():
            return 2, f"基线文件不存在: {baseline_path}"

    baseline_data = load_json(baseline_path)
    baseline_kind = detect_kind(baseline_data)
    if baseline_kind != "recipe":
        return 2, f"基线类型不一致：当前 recipe，基线 {baseline_kind}"

    diff = diff_reports(result, baseline_data, "recipe")
    print(f"[INFO] 基线: {baseline_path}")
    print(
        f"[DIFF] 退化 {diff['regressions']} · 改善 {diff['improvements']} · "
        f"新增 {diff['added']} · 移除 {diff['removed']}"
    )
    for item in [*diff["items"], *diff["metrics"]]:
        if item["change"] in ("regressed", "removed"):
            print(f"[DIFF] {item['label']}: {item['before']} -> {item['after']}")
    return (1 if diff["regressions"] else 0), ""


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        inputs = _parse_inputs(args.inputs)
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    try:
        recipe = load_recipe(args.recipe)
    except Exception as exc:
        print(f"[FAIL] 配方加载失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    inputs, missing = _resolve_inputs(recipe.inputs, inputs)
    if missing:
        hints = " ".join(f"--inputs {name}=..." for name in missing)
        print(
            f"[FAIL] 缺少必填输入: {', '.join(missing)}（用 {hints} 传入）",
            file=sys.stderr,
        )
        return 2

    need_browser = any(step.uses.startswith("browser_") for step in recipe.steps)
    need_http = any(step.uses.startswith("http_") for step in recipe.steps)

    http_client = None
    context = nullcontext(None)
    try:
        if need_http:
            from anti_crawl_kit.http import AntiRiskHttpClient

            http_client = AntiRiskHttpClient("ack-recipe", timeout=30, max_retries=2)
        if need_browser:
            from anti_crawl_kit.session import BrowserSession

            context = BrowserSession(
                headless=not args.headed,
                skip_version_check=True,
                channel=args.channel or None,
            )
        with context as page:
            run = run_recipe(
                recipe,
                inputs=inputs,
                page=page,
                http_client=http_client,
                on_event=_print_event,
            )
    except Exception as exc:
        print(f"[FAIL] 运行失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if http_client is not None and hasattr(http_client, "close"):
            http_client.close()

    result = run.to_dict()
    if run.status == "needs_agent":
        handoff = result.get("handoff") or {}
        print(
            f"[AGENT] 配方停在检查点 {handoff.get('step_id') or '-'}："
            f"{handoff.get('prompt') or '需要 agent 判断'}",
            file=sys.stderr,
        )
        print("[INFO] needs_agent 中间态不落盘；用 ack-ui 或 resume_recipe 继续", file=sys.stderr)
        return 1

    try:
        paths = persist_run(
            result,
            args.runs,
            recipe.id,
            extra={
                "cli": {
                    "recipe_path": str(args.recipe),
                    "inputs": redact_mapping(inputs),
                    "with_browser": need_browser,
                }
            },
        )
    except Exception as exc:
        print(f"[FAIL] 落盘失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"[OK] 配方 {recipe.id}: {run.status}")
    print(f"[OK] 报告: {paths['html']}")
    print(f"[INFO] 最新: {paths['latest_html']}")

    baseline_code = 0
    if args.baseline:
        baseline_code, error = _baseline_diff(paths["json"], args.baseline, result)
        if error:
            print(f"[FAIL] {error}", file=sys.stderr)
            return 2

    if run.status != "done":
        print(f"[FAIL] 配方状态 {run.status}: {run.error}", file=sys.stderr)
        return 1
    return baseline_code


if __name__ == "__main__":
    raise SystemExit(main())
