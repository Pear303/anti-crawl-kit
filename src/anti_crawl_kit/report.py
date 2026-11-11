"""单文件 HTML 报告。

把 ``ack-audit`` / ``ack-health`` / ``ack-smoke`` / ``ack-assess`` 以及
``RunReport`` 的 JSON 输出渲染成一个自包含的 HTML：样式和脚本都内嵌，
不依赖 CDN，双击就能看，也能直接当附件发给别人。

用法::

    from anti_crawl_kit.report import render_html, write_html

    html = render_html(data, kind="auto", title="掘金体检")
    write_html(data, "runs/health.html")

设计约束：

- 不引入模板引擎。所有动态文本都经过 ``html.escape``，避免平台返回内容
  造成 XSS。
- 字段缺失时降级成 ``-``，不抛异常。报告本身不能因为一条脏数据打不开。
- 深色模式跟随系统，打印时自动去掉阴影。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = [
    "detect_kind",
    "render_html",
    "render_index",
    "write_html",
    "load_json",
]

# ── 状态到颜色的映射 ──────────────────────────────────

_TONE_MAP = {
    # 通过
    "ok": "ok",
    "pass": "ok",
    "passed": "ok",
    "completed": "ok",
    "complete": "ok",
    "done": "ok",
    "go": "ok",
    "healthy": "ok",
    # 警告
    "warn": "warn",
    "warning": "warn",
    "degraded": "warn",
    "conditional_go": "warn",
    "login_required": "warn",
    "signature_required": "warn",
    "partial": "warn",
    # 失败
    "fail": "fail",
    "failed": "fail",
    "error": "fail",
    "blocked": "fail",
    "captcha": "fail",
    "challenge": "fail",
    "rate_limited": "fail",
    "risk": "fail",
    "signature_failed": "fail",
    "no_go": "fail",
    "empty": "fail",
    # 未测 / 跳过
    "skip": "muted",
    "skipped": "muted",
    "not_tested": "muted",
    "untested": "muted",
    "none": "muted",
}

_TONE_LABEL = {
    "ok": "通过",
    "warn": "警告",
    "fail": "失败",
    "muted": "未测",
    "info": "信息",
}


def _tone(status: Any) -> str:
    return _TONE_MAP.get(str(status or "").strip().lower(), "info")


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _num(value: Any, default: str = "-") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def _stat(label: str, value: Any, tone: str = "info") -> dict[str, str]:
    return {"label": label, "value": _num(value), "tone": tone}


# ── 类型识别 ──────────────────────────────────────────


def detect_kind(data: Mapping[str, Any] | None) -> str:
    """根据 JSON 结构判断报告类型。

    顺序很重要：``audit`` 也是一份 ``RunReport``，必须先看 ``meta.path_matrix``，
    否则会被误判成普通运行报告。
    """
    if not isinstance(data, Mapping):
        return "run"

    meta = data.get("meta")
    if isinstance(meta, Mapping) and meta.get("path_matrix") is not None:
        return "audit"
    if "decision" in data and "gates" in data:
        return "assess"

    results = data.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, Mapping):
            if "dimension" in first:
                return "health"
            if "kind" in first and "status" in first:
                return "smoke"

    if "outcomes" in data:
        return "run"
    if "recipe_id" in data and "steps" in data:
        return "recipe"
    if "summary" in data and isinstance(results, list):
        return "health"
    return "run"


# ── 各类型归一化 ──────────────────────────────────────


def _model_audit(data: Mapping[str, Any]) -> dict[str, Any]:
    meta = data.get("meta") if isinstance(data.get("meta"), Mapping) else {}
    rows = meta.get("path_matrix") or []
    recommendation = meta.get("recommendation") or {}
    hard_stop = str(meta.get("hard_stop") or "")

    cells = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        stop = str(row.get("stop_reason") or "not_tested")
        status = row.get("status") or ""
        latency = row.get("latency_ms") or 0
        score = row.get("total_score")
        fields = row.get("fields") or {}
        detail = stop
        if status:
            detail += f" · HTTP {status}"
        if latency:
            detail += f" · {latency}ms"
        if fields:
            detail += f" · {len(fields)} 字段"
        cells.append(
            {
                "name": row.get("label") or row.get("path") or "-",
                "sub": f"{row.get('path', '-')} · {row.get('layer', '-')}",
                "status": stop,
                "tone": _tone(stop),
                "detail": detail,
                "score": _num(score) if score is not None else "",
                "note": str(row.get("note") or row.get("rejected") or ""),
            }
        )

    tested = [row for row in rows if isinstance(row, Mapping) and row.get("tested")]
    completed = [
        row
        for row in rows
        if isinstance(row, Mapping) and row.get("stop_reason") == "completed"
    ]
    untested = len(rows) - len(tested)

    if hard_stop:
        verdict, tone = f"硬停 · {hard_stop}", "fail"
    elif not tested:
        verdict, tone = "未执行", "muted"
    elif untested:
        verdict, tone = f"部分通过（{untested} 条未测）", "warn"
    elif len(completed) == len(tested):
        verdict, tone = "全部通过", "ok"
    else:
        verdict, tone = "部分通过", "warn"

    sections: list[dict[str, Any]] = [
        {
            "type": "matrix",
            "title": "路径矩阵",
            "hint": "按状态筛选；灰色是未测路径，结论不要算上它们。",
            "cells": cells,
        }
    ]

    if recommendation:
        primary = recommendation.get("primary") or {}
        fallback = recommendation.get("fallback") or {}
        cards = [
            {
                "title": "主路径",
                "value": primary.get("path") or "未选出",
                "detail": primary.get("reason") or "",
                "tone": "ok" if primary else "muted",
            },
            {
                "title": "兜底路径",
                "value": fallback.get("path") or "未选出",
                "detail": fallback.get("reason") or "",
                "tone": "warn" if fallback else "muted",
            },
            {
                "title": "生产可用",
                "value": "是" if recommendation.get("production_ready") else "否",
                "detail": recommendation.get("last_verified") or "",
                "tone": "ok" if recommendation.get("production_ready") else "muted",
            },
        ]
        sections.append({"type": "cards", "title": "选型建议", "cards": cards})

        rejected = recommendation.get("rejected") or []
        if rejected:
            sections.append(
                {
                    "type": "table",
                    "title": "被排除的路径",
                    "columns": [
                        {"key": "path", "label": "路径", "mono": True},
                        {"key": "layer", "label": "层级", "mono": True},
                        {"key": "reason", "label": "原因"},
                    ],
                    "rows": [dict(item) for item in rejected if isinstance(item, Mapping)],
                }
            )

        gaps = recommendation.get("acceptance_gaps") or []
        if gaps:
            sections.append({"type": "list", "title": "验收缺口", "items": list(gaps)})

    sections.append({"type": "raw", "title": "原始 JSON", "data": data})

    return {
        "kind": "audit",
        "title": f"路径审计 · {data.get('platform', '-')}",
        "subtitle": str(meta.get("test_url") or ""),
        "verdict": verdict,
        "tone": tone,
        "started_at": data.get("started_at", ""),
        "finished_at": data.get("finished_at", ""),
        "stats": [
            _stat("路径", len(rows)),
            _stat("完成", len(completed), "ok" if completed else "muted"),
            _stat("未测", untested, "muted" if untested else "ok"),
            _stat("硬停", hard_stop or "无", "fail" if hard_stop else "ok"),
        ],
        "sections": sections,
    }


def _model_health(data: Mapping[str, Any]) -> dict[str, Any]:
    results = [item for item in (data.get("results") or []) if isinstance(item, Mapping)]
    summary = data.get("summary") if isinstance(data.get("summary"), Mapping) else {}
    fail = int(summary.get("FAIL") or 0)
    warn = int(summary.get("WARN") or 0)
    ok = int(summary.get("OK") or 0)
    skip = int(summary.get("SKIP") or 0)

    if fail:
        verdict, tone = f"{fail} 项失败", "fail"
    elif warn:
        verdict, tone = f"{warn} 项警告", "warn"
    elif not results:
        verdict, tone = "无数据", "muted"
    else:
        verdict, tone = "全部通过", "ok"

    # 按 dimension 分组，保持原始顺序。
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for item in results:
        groups.setdefault(str(item.get("dimension") or "other"), []).append(item)

    sections: list[dict[str, Any]] = []
    for dimension, rows in groups.items():
        sections.append(
            {
                "type": "table",
                "title": dimension,
                "columns": [
                    {"key": "platform", "label": "平台", "mono": True},
                    {"key": "status", "label": "状态", "tone": True},
                    {"key": "detail", "label": "详情"},
                ],
                "rows": [
                    {
                        "platform": row.get("platform", "-"),
                        "status": row.get("status", "-"),
                        "detail": row.get("detail", ""),
                        "tone": _tone(row.get("status")),
                    }
                    for row in rows
                ],
            }
        )

    sections.append({"type": "raw", "title": "原始 JSON", "data": data})

    return {
        "kind": "health",
        "title": "平台健康体检",
        "subtitle": f"{len(groups)} 个检查维度 · {len(results)} 条结果",
        "verdict": verdict,
        "tone": tone,
        "started_at": data.get("started_at", ""),
        "finished_at": data.get("finished_at", ""),
        "stats": [
            _stat("OK", ok, "ok"),
            _stat("WARN", warn, "warn" if warn else "muted"),
            _stat("FAIL", fail, "fail" if fail else "muted"),
            _stat("SKIP", skip, "muted"),
        ],
        "sections": sections,
    }


def _model_smoke(data: Mapping[str, Any]) -> dict[str, Any]:
    results = [item for item in (data.get("results") or []) if isinstance(item, Mapping)]
    summary = data.get("summary") if isinstance(data.get("summary"), Mapping) else {}
    passed = int(summary.get("PASS") or 0)
    failed = int(summary.get("FAIL") or 0)
    skipped = int(summary.get("SKIP") or 0)
    elapsed = sum(int(item.get("elapsed_ms") or 0) for item in results)

    if failed:
        verdict, tone = f"{failed} 个用例失败", "fail"
    elif not results:
        verdict, tone = "无数据", "muted"
    elif passed and skipped:
        verdict, tone = f"通过（{skipped} 个跳过）", "warn"
    elif skipped and not passed:
        verdict, tone = "全部跳过", "muted"
    else:
        verdict, tone = "全部通过", "ok"

    rows = []
    for item in results:
        rows.append(
            {
                "name": item.get("name", "-"),
                "kind": item.get("kind", "-"),
                "status": item.get("status", "-"),
                "actual": item.get("actual") or item.get("detail") or "-",
                "elapsed": f"{int(item.get('elapsed_ms') or 0)}ms",
                "error": item.get("error", ""),
                "tone": _tone(item.get("status")),
            }
        )

    sections: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": "用例",
            "hint": "FAIL 会阻断发版；SKIP 在 --strict 下也算失败。",
            "columns": [
                {"key": "name", "label": "用例", "mono": True},
                {"key": "kind", "label": "类型", "mono": True},
                {"key": "status", "label": "状态", "tone": True},
                {"key": "actual", "label": "结果"},
                {"key": "elapsed", "label": "耗时", "mono": True},
                {"key": "error", "label": "错误"},
            ],
            "rows": rows,
        },
        {"type": "raw", "title": "原始 JSON", "data": data},
    ]

    return {
        "kind": "smoke",
        "title": "真实站点冒烟",
        "subtitle": f"{len(results)} 个用例 · 累计 {elapsed / 1000:.1f}s",
        "verdict": verdict,
        "tone": tone,
        "started_at": data.get("started_at", ""),
        "finished_at": data.get("finished_at", ""),
        "stats": [
            _stat("PASS", passed, "ok" if passed else "muted"),
            _stat("FAIL", failed, "fail" if failed else "muted"),
            _stat("SKIP", skipped, "muted"),
            _stat("耗时", f"{elapsed / 1000:.1f}s"),
        ],
        "sections": sections,
    }


def _model_assess(data: Mapping[str, Any]) -> dict[str, Any]:
    gates = [item for item in (data.get("gates") or []) if isinstance(item, Mapping)]
    decision = str(data.get("decision") or "").lower()
    decision_label = {
        "go": "GO",
        "conditional_go": "CONDITIONAL GO",
        "no_go": "NO-GO",
    }.get(decision, decision.upper() or "-")
    tone = _tone(decision)

    gate_cells = []
    for gate in gates:
        gate_cells.append(
            {
                "gate": gate.get("gate", "-"),
                "name": gate.get("name", "-"),
                "status": gate.get("status", "-"),
                "tone": _tone(gate.get("status")),
                "summary": gate.get("summary", ""),
                "elapsed": f"{int(gate.get('elapsed_ms') or 0)}ms",
                "gaps": list(gate.get("gaps") or []),
            }
        )

    passed = sum(1 for gate in gates if str(gate.get("status")).upper() == "PASS")
    sections: list[dict[str, Any]] = [
        {
            "type": "gates",
            "title": "验证关卡",
            "gates": gate_cells,
        }
    ]

    gaps = list(data.get("gaps") or [])
    if gaps:
        sections.append({"type": "list", "title": "缺口", "items": gaps, "tone": "warn"})
    actions = list(data.get("next_actions") or [])
    if actions:
        sections.append({"type": "list", "title": "下一步", "items": actions})

    sections.append({"type": "raw", "title": "原始 JSON", "data": data})

    return {
        "kind": "assess",
        "title": f"技术验证 · {data.get('platform', '-')}",
        "subtitle": str(data.get("url") or ""),
        "verdict": decision_label,
        "tone": tone,
        "started_at": data.get("started_at", ""),
        "finished_at": data.get("finished_at", ""),
        "stats": [
            _stat("决策", decision_label, tone),
            _stat("评分", f"{data.get('score', 0)}/100"),
            _stat("关卡", f"{passed}/{len(gates)}", "ok" if gates and passed == len(gates) else "warn"),
            _stat("缺口", len(gaps), "warn" if gaps else "ok"),
        ],
        "sections": sections,
    }


def _model_run(data: Mapping[str, Any]) -> dict[str, Any]:
    status = str(data.get("status") or "-")
    tone = _tone(status)
    expected = data.get("expected_count")
    actual = data.get("actual_count")
    coverage = data.get("item_coverage")
    degraded = list(data.get("degraded_reasons") or [])
    stop_reason = data.get("stop_reason")

    cards = [
        {
            "title": "预期条目",
            "value": _num(expected, "未知"),
            "detail": "expected_count",
            "tone": "info",
        },
        {
            "title": "实际条目",
            "value": _num(actual, "0"),
            "detail": "actual_count = 0 一律判 empty",
            "tone": "fail" if (actual or 0) == 0 else "ok",
        },
        {
            "title": "条目覆盖率",
            "value": _pct(coverage) if coverage is not None else "未知",
            "detail": "item_coverage",
            "tone": "ok" if coverage == 1 else ("warn" if coverage is not None else "muted"),
        },
        {
            "title": "降级原因",
            "value": str(len(degraded)),
            "detail": "、".join(str(item) for item in degraded) or "无",
            "tone": "warn" if degraded else "ok",
        },
    ]

    outcomes = [item for item in (data.get("outcomes") or []) if isinstance(item, Mapping)]
    rows = []
    for item in outcomes:
        ok = bool(item.get("ok"))
        rows.append(
            {
                "path": item.get("path") or item.get("name") or "-",
                "ok": "OK" if ok else "FAIL",
                "stop_reason": item.get("stop_reason") or item.get("error") or "-",
                "latency": f"{int(item.get('latency_ms') or 0)}ms",
                "tone": "ok" if ok else "fail",
            }
        )

    sections: list[dict[str, Any]] = [
        {"type": "cards", "title": "静默失效四字段", "cards": cards},
    ]
    if rows:
        sections.append(
            {
                "type": "table",
                "title": "明细",
                "columns": [
                    {"key": "path", "label": "条目", "mono": True},
                    {"key": "ok", "label": "结果", "tone": True},
                    {"key": "stop_reason", "label": "原因"},
                    {"key": "latency", "label": "耗时", "mono": True},
                ],
                "rows": rows,
            }
        )
    if stop_reason:
        sections.append(
            {
                "type": "list",
                "title": "停止原因",
                "items": [str(stop_reason)],
                "tone": "fail" if _tone(stop_reason) == "fail" else "warn",
            }
        )
    sections.append({"type": "raw", "title": "原始 JSON", "data": data})

    return {
        "kind": "run",
        "title": f"运行报告 · {data.get('task', '-')}",
        "subtitle": f"平台 {data.get('platform', '-')} · 模式 {data.get('mode', '-') or '-'}",
        "verdict": status.upper(),
        "tone": tone,
        "started_at": data.get("started_at", ""),
        "finished_at": data.get("finished_at", ""),
        "stats": [
            _stat("条目", data.get("total", 0)),
            _stat("成功", data.get("ok", 0), "ok"),
            _stat("失败", data.get("failed", 0), "fail" if data.get("failed") else "muted"),
            _stat("成功率", _pct(data.get("success_rate"))),
        ],
        "sections": sections,
    }


def _model_recipe(data: Mapping[str, Any]) -> dict[str, Any]:
    """配方执行报告（``RecipeRun.to_dict()``）。"""
    steps = [item for item in (data.get("steps") or []) if isinstance(item, Mapping)]
    status = str(data.get("status") or "-")
    tone = _tone(status)
    ok = sum(1 for item in steps if item.get("status") == "ok")
    failed = sum(1 for item in steps if item.get("status") == "failed")
    skipped = sum(1 for item in steps if item.get("status") == "skipped")
    elapsed = sum(int(item.get("elapsed_ms") or 0) for item in steps)

    rows = []
    for item in steps:
        step_status = str(item.get("status") or "-")
        rows.append(
            {
                "id": item.get("id", "-"),
                "uses": item.get("uses", "-"),
                "status": step_status,
                "elapsed": f"{int(item.get('elapsed_ms') or 0)}ms",
                "error": item.get("error", ""),
                "tone": _tone(step_status),
            }
        )

    sections: list[dict[str, Any]] = [
        {
            "type": "table",
            "title": "步骤",
            "columns": [
                {"key": "id", "label": "步骤", "mono": True},
                {"key": "uses", "label": "能力", "mono": True},
                {"key": "status", "label": "状态", "tone": True},
                {"key": "elapsed", "label": "耗时", "mono": True},
                {"key": "error", "label": "错误"},
            ],
            "rows": rows,
        }
    ]

    handoff = data.get("handoff")
    if isinstance(handoff, Mapping) and handoff:
        sections.insert(
            0,
            {
                "type": "cards",
                "title": "等待人工判断",
                "cards": [
                    {
                        "title": "检查点",
                        "value": handoff.get("step_id") or "-",
                        "detail": handoff.get("prompt") or "",
                        "tone": "warn",
                    },
                    {
                        "title": "原因",
                        "value": handoff.get("reason") or "agent",
                        "detail": handoff.get("error") or "",
                        "tone": "warn",
                    },
                ],
            },
        )

    sections.append({"type": "raw", "title": "原始 JSON", "data": data})

    return {
        "kind": "recipe",
        "title": f"配方执行 · {data.get('recipe_id', '-')}",
        "subtitle": f"run_id {data.get('run_id', '-')}",
        "verdict": status.upper(),
        "tone": tone,
        "started_at": data.get("started_at", ""),
        "finished_at": data.get("finished_at", ""),
        "stats": [
            _stat("步骤", len(steps)),
            _stat("成功", ok, "ok"),
            _stat("失败", failed, "fail" if failed else "muted"),
            _stat("跳过", skipped, "muted"),
            _stat("耗时", f"{elapsed / 1000:.1f}s"),
        ],
        "sections": sections,
    }


_MODEL_BUILDERS = {
    "audit": _model_audit,
    "health": _model_health,
    "smoke": _model_smoke,
    "assess": _model_assess,
    "run": _model_run,
    "recipe": _model_recipe,
}


# ── HTML 片段 ────────────────────────────────────────


def _render_stats(stats: Iterable[Mapping[str, Any]]) -> str:
    items = []
    for stat in stats:
        items.append(
            f'<div class="stat tone-{_esc(stat.get("tone", "info"))}">'
            f'<div class="stat-label">{_esc(stat.get("label"))}</div>'
            f'<div class="stat-value">{_esc(stat.get("value"))}</div>'
            f"</div>"
        )
    return f'<section class="stats">{"".join(items)}</section>'


def _render_filters() -> str:
    buttons = [("all", "全部"), ("ok", "通过"), ("warn", "警告"), ("fail", "失败"), ("muted", "未测")]
    html_buttons = "".join(
        f'<button type="button" class="filter{" active" if key == "all" else ""}" '
        f'data-filter="{key}">{label}</button>'
        for key, label in buttons
    )
    return f'<div class="filters">{html_buttons}</div>'


def _render_matrix(section: Mapping[str, Any]) -> str:
    cells = []
    for cell in section.get("cells") or []:
        note = ""
        if cell.get("note"):
            note = f'<div class="cell-note">{_esc(cell.get("note"))}</div>'
        score = ""
        if cell.get("score"):
            score = f'<span class="cell-score">{_esc(cell.get("score"))}/25</span>'
        cells.append(
            f'<article class="cell tone-{_esc(cell.get("tone", "info"))}" '
            f'data-tone="{_esc(cell.get("tone", "info"))}">'
            f'<div class="cell-top"><span class="dot"></span>'
            f'<span class="cell-name">{_esc(cell.get("name"))}</span>{score}</div>'
            f'<div class="cell-sub">{_esc(cell.get("sub"))}</div>'
            f'<div class="cell-detail">{_esc(cell.get("detail"))}</div>'
            f"{note}</article>"
        )
    hint = f'<p class="hint">{_esc(section.get("hint"))}</p>' if section.get("hint") else ""
    return (
        f'<section class="section" data-section>'
        f'<div class="section-head"><h2>{_esc(section.get("title"))}</h2>'
        f"{_render_filters()}</div>{hint}"
        f'<div class="matrix">{"".join(cells)}</div></section>'
    )


def _render_table(section: Mapping[str, Any]) -> str:
    columns = [col for col in (section.get("columns") or []) if isinstance(col, Mapping)]
    head = "".join(f'<th>{_esc(col.get("label") or col.get("key"))}</th>' for col in columns)
    body = []
    for row in section.get("rows") or []:
        if not isinstance(row, Mapping):
            continue
        tone = row.get("tone") or ""
        cells = []
        for col in columns:
            key = str(col.get("key") or "")
            value = row.get(key, "")
            cls = "mono" if col.get("mono") else ""
            if col.get("tone") and value:
                cells.append(
                    f'<td><span class="pill tone-{_esc(_tone(value))}">{_esc(value)}</span></td>'
                )
            else:
                cells.append(f'<td class="{cls}">{_esc(value)}</td>')
        body.append(f'<tr data-tone="{_esc(tone)}">{"".join(cells)}</tr>')
    hint = f'<p class="hint">{_esc(section.get("hint"))}</p>' if section.get("hint") else ""
    return (
        f'<section class="section" data-section>'
        f'<div class="section-head"><h2>{_esc(section.get("title"))}</h2>'
        f"{_render_filters()}</div>{hint}"
        f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div></section>'
    )


def _render_gates(section: Mapping[str, Any]) -> str:
    cards = []
    for gate in section.get("gates") or []:
        gaps = ""
        if gate.get("gaps"):
            gaps = '<ul class="gate-gaps">' + "".join(
                f"<li>{_esc(item)}</li>" for item in gate.get("gaps")
            ) + "</ul>"
        cards.append(
            f'<article class="gate tone-{_esc(gate.get("tone", "info"))}" '
            f'data-tone="{_esc(gate.get("tone", "info"))}">'
            f'<div class="gate-head"><span class="gate-id">{_esc(gate.get("gate"))}</span>'
            f'<span class="pill tone-{_esc(gate.get("tone", "info"))}">'
            f'{_esc(gate.get("status"))}</span></div>'
            f'<div class="gate-name">{_esc(gate.get("name"))}</div>'
            f'<div class="gate-sum">{_esc(gate.get("summary"))}</div>'
            f'<div class="gate-time">{_esc(gate.get("elapsed"))}</div>{gaps}</article>'
        )
    return (
        f'<section class="section" data-section>'
        f'<div class="section-head"><h2>{_esc(section.get("title"))}</h2>'
        f"{_render_filters()}</div>"
        f'<div class="gates">{"".join(cards)}</div></section>'
    )


def _render_cards(section: Mapping[str, Any]) -> str:
    cards = []
    for card in section.get("cards") or []:
        cards.append(
            f'<article class="card tone-{_esc(card.get("tone", "info"))}">'
            f'<div class="card-title">{_esc(card.get("title"))}</div>'
            f'<div class="card-value">{_esc(card.get("value"))}</div>'
            f'<div class="card-detail">{_esc(card.get("detail"))}</div></article>'
        )
    return (
        f'<section class="section" data-section>'
        f'<div class="section-head"><h2>{_esc(section.get("title"))}</h2></div>'
        f'<div class="cards">{"".join(cards)}</div></section>'
    )


def _render_list(section: Mapping[str, Any]) -> str:
    tone = _esc(section.get("tone", "info"))
    items = "".join(f"<li>{_esc(item)}</li>" for item in section.get("items") or [])
    return (
        f'<section class="section tone-{tone}" data-section>'
        f'<div class="section-head"><h2>{_esc(section.get("title"))}</h2></div>'
        f'<ul class="list">{items}</ul></section>'
    )


def _render_raw(section: Mapping[str, Any]) -> str:
    payload = json.dumps(section.get("data"), ensure_ascii=False, indent=2, default=str)
    return (
        f'<section class="section" data-section>'
        f'<div class="section-head"><h2>{_esc(section.get("title"))}</h2>'
        f'<button type="button" class="ghost" data-copy>复制</button></div>'
        f'<details><summary>展开原始 JSON</summary>'
        f'<pre class="raw">{_esc(payload)}</pre></details></section>'
    )


_SECTION_RENDERERS = {
    "matrix": _render_matrix,
    "table": _render_table,
    "gates": _render_gates,
    "cards": _render_cards,
    "list": _render_list,
    "raw": _render_raw,
}


# ── 页面骨架 ─────────────────────────────────────────

_CSS = """
:root {
  --bg: #f5f6f8;
  --panel: #ffffff;
  --text: #1f2328;
  --muted: #656d76;
  --line: #e3e6ea;
  --ok: #1a7f37;
  --ok-bg: #e9f6ec;
  --warn: #9a6700;
  --warn-bg: #fff7e0;
  --fail: #cf222e;
  --fail-bg: #ffecec;
  --info: #0969da;
  --info-bg: #eaf2fd;
  --muted-bg: #f0f2f4;
  --radius: 10px;
  --shadow: 0 1px 2px rgba(31, 35, 40, 0.06);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1216;
    --panel: #171b21;
    --text: #e6edf3;
    --muted: #9aa4b2;
    --line: #2a313a;
    --ok: #4ac26b;
    --ok-bg: #12261a;
    --warn: #d29922;
    --warn-bg: #2a2110;
    --fail: #ff7b72;
    --fail-bg: #2d1517;
    --info: #6cb6ff;
    --info-bg: #10233a;
    --muted-bg: #1c222a;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC",
        "PingFang SC", "Microsoft YaHei", sans-serif;
}
.page { max-width: 1120px; margin: 0 auto; padding: 40px 24px 64px; }
.head { margin-bottom: 24px; }
.kicker {
  font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 8px;
}
h1 { font-size: 28px; line-height: 1.25; margin: 0 0 8px; font-weight: 650; }
h2 { font-size: 15px; margin: 0; font-weight: 600; }
.sub { color: var(--muted); margin: 0 0 14px; word-break: break-all; }
.meta { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; color: var(--muted); font-size: 12px; }
.badge { padding: 4px 12px; font-size: 12px; letter-spacing: 0.02em; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 28px; }
.stat { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px 16px; box-shadow: var(--shadow); }
.stat-label { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.stat-value { font-size: 22px; font-weight: 650; font-variant-numeric: tabular-nums; }
.section { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 20px; margin-bottom: 16px; box-shadow: var(--shadow); }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.hint { color: var(--muted); font-size: 12px; margin: -4px 0 12px; }
.filters { display: flex; gap: 6px; flex-wrap: wrap; }
.filter, .ghost {
  border: 1px solid var(--line); background: transparent; color: var(--muted);
  border-radius: 999px; padding: 3px 10px; font-size: 12px; cursor: pointer;
  font-family: inherit;
}
.filter:hover, .ghost:hover { color: var(--text); border-color: var(--muted); }
.filter.active { background: var(--text); border-color: var(--text); color: var(--bg); }
.matrix { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; }
.cell { border: 1px solid var(--line); border-left-width: 3px; border-radius: 8px; padding: 10px 12px; background: var(--bg); }
.cell-top { display: flex; align-items: center; gap: 7px; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex: none; }
.cell-name { font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cell-score { margin-left: auto; font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.cell-sub { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; color: var(--muted); margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cell-detail { font-size: 12px; margin-top: 4px; }
.cell-note { font-size: 11px; color: var(--muted); margin-top: 4px; }
.tone-ok { border-left-color: var(--ok); }
.tone-ok .dot { background: var(--ok); }
.tone-warn { border-left-color: var(--warn); }
.tone-warn .dot { background: var(--warn); }
.tone-fail { border-left-color: var(--fail); }
.tone-fail .dot { background: var(--fail); }
.tone-muted { border-left-color: var(--line); }
.tone-muted .dot { background: var(--muted); }
.stat.tone-ok .stat-value { color: var(--ok); }
.stat.tone-warn .stat-value { color: var(--warn); }
.stat.tone-fail .stat-value { color: var(--fail); }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; background: var(--muted-bg); color: var(--muted); }
.pill.tone-ok { background: var(--ok-bg); color: var(--ok); }
.pill.tone-warn { background: var(--warn-bg); color: var(--warn); }
.pill.tone-fail { background: var(--fail-bg); color: var(--fail); }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 500; font-size: 12px; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
td.mono, .mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
.gates { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
.gate { border: 1px solid var(--line); border-top-width: 3px; border-radius: 8px; padding: 12px; background: var(--bg); }
.gate-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.gate-id { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; color: var(--muted); }
.gate-name { font-weight: 600; margin-top: 6px; }
.gate-sum { font-size: 12px; color: var(--muted); margin-top: 4px; }
.gate-time { font-size: 11px; color: var(--muted); margin-top: 6px; font-variant-numeric: tabular-nums; }
.gate-gaps { margin: 8px 0 0; padding-left: 18px; font-size: 12px; color: var(--warn); }
.tone-ok.gate { border-top-color: var(--ok); }
.tone-warn.gate { border-top-color: var(--warn); }
.tone-fail.gate { border-top-color: var(--fail); }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.card { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: var(--bg); }
.card-title { font-size: 12px; color: var(--muted); }
.card-value { font-size: 18px; font-weight: 650; margin-top: 4px; word-break: break-all; }
.card-detail { font-size: 12px; color: var(--muted); margin-top: 4px; }
.list { margin: 0; padding-left: 20px; }
.list li { margin: 4px 0; }
details { margin-top: 4px; }
summary { cursor: pointer; color: var(--muted); font-size: 12px; }
pre.raw {
  background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
  padding: 12px; overflow: auto; max-height: 520px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px; line-height: 1.5;
}
footer { color: var(--muted); font-size: 12px; margin-top: 28px; text-align: center; }
[hidden] { display: none !important; }
@media print {
  body { background: #fff; }
  .page { max-width: none; padding: 0; }
  .section, .stat { box-shadow: none; break-inside: avoid; }
  .filters, .ghost { display: none; }
}
"""

_JS = """
document.querySelectorAll('[data-section]').forEach(function (section) {
  var buttons = section.querySelectorAll('[data-filter]');
  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tone = btn.dataset.filter;
      section.querySelectorAll('[data-tone]').forEach(function (el) {
        el.hidden = tone !== 'all' && el.dataset.tone !== tone;
      });
      buttons.forEach(function (other) {
        other.classList.toggle('active', other === btn);
      });
    });
  });
});
document.querySelectorAll('[data-copy]').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var pre = btn.closest('.section').querySelector('pre.raw');
    if (!pre) return;
    navigator.clipboard.writeText(pre.textContent || '').then(function () {
      var old = btn.textContent;
      btn.textContent = '已复制';
      setTimeout(function () { btn.textContent = old; }, 1200);
    });
  });
});
"""


def _render_page(model: Mapping[str, Any], *, generated_at: str = "") -> str:
    title = str(model.get("title") or "报告")
    tone = str(model.get("tone") or "info")
    verdict = str(model.get("verdict") or "-")

    meta_parts = []
    if model.get("started_at"):
        meta_parts.append(f'<span>开始 {_esc(model.get("started_at"))}</span>')
    if model.get("finished_at"):
        meta_parts.append(f'<span>结束 {_esc(model.get("finished_at"))}</span>')
    if generated_at:
        meta_parts.append(f"<span>生成 {_esc(generated_at)}</span>")

    sections = []
    for section in model.get("sections") or []:
        renderer = _SECTION_RENDERERS.get(str(section.get("type")))
        if renderer is not None:
            sections.append(renderer(section))

    subtitle = ""
    if model.get("subtitle"):
        subtitle = f'<p class="sub">{_esc(model.get("subtitle"))}</p>'

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<main class="page">
  <header class="head">
    <div class="kicker">anti-crawl-kit · {_esc(model.get("kind", "report"))}</div>
    <h1>{_esc(title)}</h1>
    {subtitle}
    <div class="meta">
      <span class="pill tone-{_esc(tone)} badge">{_esc(verdict)}</span>
      {''.join(meta_parts)}
    </div>
  </header>
  {_render_stats(model.get("stats") or [])}
  {''.join(sections)}
  <footer>由 anti-crawl-kit 生成 · 报告只含本地数据</footer>
</main>
<script>{_JS}</script>
</body>
</html>
"""


# ── 对外接口 ─────────────────────────────────────────


def render_html(
    data: Mapping[str, Any],
    *,
    kind: str = "auto",
    title: str = "",
    generated_at: str = "",
) -> str:
    """把一份报告数据渲染成完整 HTML。

    Args:
        data: 各模块 ``to_dict()`` 的结果。
        kind: ``audit`` / ``health`` / ``smoke`` / ``assess`` / ``run`` /
            ``recipe`` / ``auto``。
        title: 覆盖默认标题。
        generated_at: 生成时间（默认不显示）。
    """
    if not isinstance(data, Mapping):
        data = {"raw": data}
    resolved = detect_kind(data) if kind in ("", "auto") else kind
    builder = _MODEL_BUILDERS.get(resolved)
    if builder is None:
        raise ValueError(f"未知报告类型: {resolved}")
    model = builder(data)
    if title:
        model["title"] = title
    return _render_page(model, generated_at=generated_at)


def write_html(
    data: Mapping[str, Any],
    path: str | Path,
    *,
    kind: str = "auto",
    title: str = "",
    generated_at: str = "",
) -> Path:
    """渲染并写入文件，返回实际路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_html(data, kind=kind, title=title, generated_at=generated_at),
        encoding="utf-8",
    )
    return target


def load_json(path: str | Path) -> dict[str, Any]:
    """读取报告 JSON；顶层必须是对象。"""
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"报告顶层必须是对象: {source}")
    return data


def render_index(
    entries: Iterable[Mapping[str, Any]],
    *,
    title: str = "报告索引",
) -> str:
    """生成一个目录页，链接到同目录下的 HTML 报告。

    ``entries`` 每项支持 ``path`` / ``title`` / ``kind`` / ``verdict`` /
    ``tone`` / ``finished_at``。
    """
    rows = []
    for entry in entries:
        tone = str(entry.get("tone") or "info")
        rows.append(
            f'<tr data-tone="{_esc(tone)}">'
            f'<td><a href="{_esc(entry.get("path"))}">{_esc(entry.get("title"))}</a></td>'
            f'<td><span class="pill tone-{_esc(tone)}">{_esc(entry.get("verdict"))}</span></td>'
            f'<td class="mono">{_esc(entry.get("kind"))}</td>'
            f'<td class="mono">{_esc(entry.get("finished_at"))}</td></tr>'
        )
    body = "".join(rows) or '<tr><td colspan="4">还没有报告</td></tr>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<main class="page">
  <header class="head">
    <div class="kicker">anti-crawl-kit</div>
    <h1>{_esc(title)}</h1>
    <p class="sub">本地生成的报告，点击查看。</p>
  </header>
  <section class="section">
    <div class="table-wrap"><table>
      <thead><tr><th>报告</th><th>结论</th><th>类型</th><th>完成时间</th></tr></thead>
      <tbody>{body}</tbody>
    </table></div>
  </section>
</main>
</body>
</html>
"""
