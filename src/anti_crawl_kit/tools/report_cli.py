#!/usr/bin/env python3
"""``ack-report`` —— 把 JSON 报告渲染成单文件 HTML。

用法::

    ack-report --input runs/audit.json                 # 生成同目录 audit.html
    ack-report --input runs/audit.json --out report.html
    ack-report --dir runs                              # 递归批量转换 + 生成 index.html
    ack-report --dir runs --out reports                # 输出到 reports/，保留子目录结构
    ack-report --dir runs --out reports/index.html     # 只指定索引位置

支持 ``audit`` / ``health`` / ``smoke`` / ``assess`` / ``run`` 五种报告，
类型默认自动识别，也可以用 ``--kind`` 指定。

退出码：
    0  成功
    2  输入不存在 / JSON 读取失败 / 类型不支持
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from anti_crawl_kit.report import (
    detect_kind,
    load_json,
    render_index,
    write_html,
)

# 目录批量转换时，这些文件名不当作报告。
_SKIP_NAMES = {"index.html", "report.html"}
# 递归扫描时跳过的目录：避免把 cookie/profile、依赖和构建产物转成 HTML。
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "profiles",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ack-report",
        description="把 ack-audit / ack-health / ack-smoke / ack-assess 的 JSON 转成 HTML",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=str, help="单个 JSON 报告")
    source.add_argument("--dir", type=str, help="报告目录（批量转换）")
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="输出路径：单文件模式为 HTML 文件；--dir 模式为输出目录，或以 .html 结尾的索引路径",
    )
    parser.add_argument(
        "--kind",
        default="auto",
        choices=["auto", "audit", "health", "smoke", "assess", "run", "recipe"],
        help="报告类型（默认自动识别）",
    )
    parser.add_argument("--title", type=str, default="", help="覆盖页面标题")
    return parser


def _convert_file(source: Path, out: Path, kind: str, title: str) -> dict[str, str]:
    data = load_json(source)
    resolved = detect_kind(data) if kind == "auto" else kind
    write_html(data, out, kind=resolved, title=title)
    return {
        "source": str(source),
        "out": str(out),
        "kind": resolved,
        "verdict": _verdict(data, resolved),
        "finished_at": str(data.get("finished_at") or ""),
    }


def _verdict(data: dict, kind: str) -> str:
    """只用于目录索引展示，和 report.py 里的完整判定保持一致的粗粒度。"""
    if kind == "assess":
        return str(data.get("decision") or "-").upper()
    if kind == "health":
        summary = data.get("summary") or {}
        if summary.get("FAIL"):
            return "FAIL"
        if summary.get("WARN"):
            return "WARN"
        return "PASS"
    if kind == "smoke":
        summary = data.get("summary") or {}
        if summary.get("FAIL"):
            return "FAIL"
        if summary.get("SKIP") and not summary.get("PASS"):
            return "SKIP"
        return "PASS"
    return str(data.get("status") or "-").upper()


def _iter_json(directory: Path) -> list[Path]:
    """递归找报告 JSON，跳过 profile/依赖/构建目录。"""
    return sorted(
        path
        for path in directory.rglob("*.json")
        if path.name not in _SKIP_NAMES
        and path.is_file()
        and not any(part in _SKIP_DIRS for part in path.relative_to(directory).parts[:-1])
    )


def _relative_link(target: Path, base: Path) -> str:
    """索引里的链接：优先相对路径，跨盘时退回绝对路径。"""
    try:
        return target.relative_to(base).as_posix()
    except ValueError:
        try:
            return Path(os.path.relpath(target, base)).as_posix()
        except ValueError:
            return target.as_posix()


def _convert_dir(directory: Path, out: Path) -> int:
    sources = _iter_json(directory)
    if not sources:
        print(f"[FAIL] 目录里没有 JSON 报告: {directory}", file=sys.stderr)
        return 2

    # --out 以 .html 结尾时只决定索引位置；否则当作输出目录，保留子目录结构
    index_is_file = out.suffix.lower() == ".html"
    out_root = out.parent if index_is_file else out
    index_path = out if index_is_file else out_root / "index.html"

    entries = []
    failures = 0
    for source in sources:
        rel = source.relative_to(directory).with_suffix(".html")
        target = source.with_suffix(".html") if index_is_file else out_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            entry = _convert_file(source, target, "auto", "")
        except Exception as exc:
            failures += 1
            print(f"[WARN] 跳过 {source}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        print(f"[OK] {source.relative_to(directory)} -> {target} ({entry['kind']})")
        entries.append(
            {
                "path": _relative_link(target, index_path.parent),
                "title": source.stem,
                "kind": entry["kind"],
                "verdict": entry["verdict"],
                "tone": _index_tone(entry["verdict"]),
                "finished_at": entry["finished_at"],
            }
        )

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        render_index(entries, title=f"{directory.name} · 报告索引"),
        encoding="utf-8",
    )
    print(f"[INFO] 索引: {index_path}")
    return 0 if entries and not failures else 1


def _index_tone(verdict: str) -> str:
    value = verdict.strip().lower()
    if value in ("pass", "ok", "go", "completed", "done"):
        return "ok"
    if value in ("fail", "no_go", "failed", "blocked", "empty"):
        return "fail"
    if value in ("warn", "warning", "conditional_go", "skip", "skipped"):
        return "warn"
    return "info"


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.input:
        source = Path(args.input)
        if not source.is_file():
            print(f"[FAIL] 文件不存在: {source}", file=sys.stderr)
            return 2
        out = Path(args.out) if args.out else source.with_suffix(".html")
        try:
            entry = _convert_file(source, out, args.kind, args.title)
        except Exception as exc:
            print(f"[FAIL] 转换失败: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        print(f"[OK] {entry['source']} -> {entry['out']} ({entry['kind']})")
        return 0

    directory = Path(args.dir)
    if not directory.is_dir():
        print(f"[FAIL] 目录不存在: {directory}", file=sys.stderr)
        return 2
    return _convert_dir(directory, Path(args.out) if args.out else directory / "index.html")


if __name__ == "__main__":
    raise SystemExit(main())
