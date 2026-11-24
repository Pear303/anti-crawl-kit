"""报告基线对比：同一类报告两次运行之间的差异。

只负责「判断」：哪些状态退化、哪些指标下降、哪些用例新增或消失。
终端摘要和 HTML 里的对比区块都读这一份结构，避免两套逻辑。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# 数字越大越差。未知状态按 WARN 处理，避免把「不认识」误判成通过。
_STATUS_RANK = {
    "OK": 0,
    "PASS": 0,
    "DONE": 0,
    "COMPLETED": 0,
    "GO": 0,
    "WARN": 1,
    "CONDITIONAL_GO": 1,
    "DEGRADED": 1,
    "FAIL": 2,
    "FAILED": 2,
    "EMPTY": 2,
    "BLOCKED": 2,
    "NO_GO": 2,
    "CHALLENGE": 2,
    "LOGIN": 2,
    "NETWORK": 2,
    "SKIP": 3,
    "SKIPPED": 3,
    "NOT_TESTED": 3,
    "NEEDS_AGENT": 3,
}

# 这些状态不参与「变好/变差」判断，只标记为变化。
_NEUTRAL = {"SKIP", "SKIPPED", "NOT_TESTED", "NEEDS_AGENT"}

_NEGATIVE_KEYS = {"FAIL", "FAILED", "ERROR", "WARN", "EMPTY", "DEGRADED", "BLOCKED", "NO_GO"}
_POSITIVE_KEYS = {"PASS", "OK", "SUCCESS", "DONE", "GO"}

CHANGE_LABELS = {
    "same": "不变",
    "regressed": "退化",
    "improved": "改善",
    "added": "新增",
    "removed": "移除",
    "changed": "变化",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rank(status: Any) -> int:
    return _STATUS_RANK.get(str(status).strip().upper(), 1)


def _status_change(before: Any, after: Any) -> str:
    before_text = str(before or "-").strip()
    after_text = str(after or "-").strip()
    if before_text == after_text:
        return "same"
    if before_text.upper() in _NEUTRAL or after_text.upper() in _NEUTRAL:
        return "changed"
    before_rank = _rank(before_text)
    after_rank = _rank(after_text)
    if after_rank > before_rank:
        return "regressed"
    if after_rank < before_rank:
        return "improved"
    return "changed"


def _summary_rows(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    keys = list(dict.fromkeys([*before.keys(), *after.keys()]))
    rows = []
    for key in keys:
        before_value = int(before.get(key, 0) or 0)
        after_value = int(after.get(key, 0) or 0)
        delta = after_value - before_value
        upper = str(key).upper()
        if delta == 0:
            change = "same"
        elif upper in _NEGATIVE_KEYS:
            change = "regressed" if delta > 0 else "improved"
        elif upper in _POSITIVE_KEYS:
            change = "regressed" if delta < 0 else "improved"
        else:
            change = "changed"
        rows.append(
            {
                "label": str(key),
                "before": before_value,
                "after": after_value,
                "delta": delta,
                "change": change,
            }
        )
    return rows


def _items(data: Mapping[str, Any], kind: str) -> list[tuple[str, str, str]]:
    """返回 (key, label, status)。不同报告的状态字段不一样，在这里归一。"""
    if kind == "audit":
        rows = _mapping(data.get("meta")).get("path_matrix") or []
        return [
            (
                str(row.get("path") or row.get("label") or "-"),
                str(row.get("label") or row.get("path") or "-"),
                str(row.get("stop_reason") or "not_tested"),
            )
            for row in rows
            if isinstance(row, Mapping)
        ]

    if kind == "assess":
        rows = data.get("gates") or []
        return [
            (
                str(row.get("gate") or "-"),
                f"{row.get('gate') or '-'} {row.get('name') or ''}".strip(),
                str(row.get("status") or "-"),
            )
            for row in rows
            if isinstance(row, Mapping)
        ]

    if kind == "run":
        rows = data.get("outcomes") or []
        items = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            key = str(row.get("path") or row.get("name") or "-")
            status = "PASS" if row.get("ok") else str(row.get("stop_reason") or "FAIL")
            items.append((key, key, status))
        return items

    if kind == "recipe":
        rows = data.get("steps") or []
        return [
            (
                str(row.get("id") or "-"),
                str(row.get("id") or "-"),
                str(row.get("status") or "-"),
            )
            for row in rows
            if isinstance(row, Mapping)
        ]

    rows = data.get("results") or []
    items = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if kind == "health":
            dimension = str(row.get("dimension") or "-")
            platform = str(row.get("platform") or "-")
            items.append(
                (
                    f"{dimension}:{platform}",
                    f"{dimension} · {platform}",
                    str(row.get("status") or "-"),
                )
            )
        else:
            key = str(row.get("name") or "-")
            items.append((key, key, str(row.get("status") or "-")))
    return items


def _metrics(data: Mapping[str, Any], kind: str) -> list[tuple[str, str, Any, str]]:
    """返回 (key, label, value, direction)，direction 为 up / down / rank。"""
    metrics: list[tuple[str, str, Any, str]] = []
    if kind == "run":
        for key, label in (
            ("actual_count", "实际条数"),
            ("expected_count", "预期条数"),
            ("success_rate", "成功率"),
        ):
            value = data.get(key)
            if value is not None:
                metrics.append((key, label, value, "up"))
    elif kind == "assess":
        if data.get("score") is not None:
            metrics.append(("score", "评分", data["score"], "up"))
        if data.get("decision"):
            metrics.append(("decision", "结论", data["decision"], "rank"))
    elif kind == "recipe":
        steps = data.get("steps") or []
        elapsed = sum(int(row.get("elapsed_ms") or 0) for row in steps if isinstance(row, Mapping))
        metrics.append(("elapsed_ms", "总耗时(ms)", elapsed, "down"))
    return metrics


def _metric_change(before: Any, after: Any, direction: str) -> str:
    if before == after:
        return "same"
    if direction == "rank":
        return _status_change(before, after)
    try:
        before_number = float(before)
        after_number = float(after)
    except (TypeError, ValueError):
        return "changed"
    if after_number == before_number:
        return "same"
    if direction == "down":
        return "regressed" if after_number > before_number else "improved"
    return "regressed" if after_number < before_number else "improved"


def diff_reports(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
    kind: str,
) -> dict[str, Any]:
    """对比当前报告和基线，返回可直接渲染/打印的差异结构。"""
    current_summary = _mapping(current.get("summary"))
    baseline_summary = _mapping(baseline.get("summary"))

    summary = _summary_rows(baseline_summary, current_summary)

    before_items = {key: (label, status) for key, label, status in _items(baseline, kind)}
    after_items = {key: (label, status) for key, label, status in _items(current, kind)}
    items = []
    regressions = 0
    improvements = 0
    added = 0
    removed = 0
    for key in sorted(dict.fromkeys([*before_items.keys(), *after_items.keys()])):
        before = before_items.get(key)
        after = after_items.get(key)
        if before is None:
            added += 1
            items.append(
                {
                    "key": key,
                    "label": after[0],
                    "before": "-",
                    "after": after[1],
                    "change": "added",
                }
            )
            continue
        if after is None:
            removed += 1
            regressions += 1
            items.append(
                {
                    "key": key,
                    "label": before[0],
                    "before": before[1],
                    "after": "-",
                    "change": "removed",
                }
            )
            continue
        change = _status_change(before[1], after[1])
        if change == "regressed":
            regressions += 1
        elif change == "improved":
            improvements += 1
        items.append(
            {
                "key": key,
                "label": after[0],
                "before": before[1],
                "after": after[1],
                "change": change,
            }
        )

    before_metrics = {
        key: (label, value, direction)
        for key, label, value, direction in _metrics(baseline, kind)
    }
    after_metrics = {
        key: (label, value, direction)
        for key, label, value, direction in _metrics(current, kind)
    }
    metrics = []
    for key in dict.fromkeys([*before_metrics.keys(), *after_metrics.keys()]):
        before = before_metrics.get(key)
        after = after_metrics.get(key)
        if before is None or after is None:
            continue
        change = _metric_change(before[1], after[1], after[2])
        if change == "regressed":
            regressions += 1
        elif change == "improved":
            improvements += 1
        metrics.append(
            {
                "key": key,
                "label": after[0],
                "before": before[1],
                "after": after[1],
                "change": change,
            }
        )

    # 没有条目可比时（例如自定义报告），用 summary 的退化兜底。
    if not items:
        regressions += sum(1 for row in summary if row["change"] == "regressed")
        improvements += sum(1 for row in summary if row["change"] == "improved")

    return {
        "kind": kind,
        "before_at": str(baseline.get("finished_at") or baseline.get("started_at") or ""),
        "after_at": str(current.get("finished_at") or current.get("started_at") or ""),
        "summary": summary,
        "metrics": metrics,
        "items": items,
        "regressions": regressions,
        "improvements": improvements,
        "added": added,
        "removed": removed,
    }
