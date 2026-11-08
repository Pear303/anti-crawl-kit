"""可判定的运行报告（run_report.json）。

为什么需要它
------------
``stop_reason`` / 覆盖率如果只写在文档里，就没法在 CI 里判定。
这个模块把一次任务的结果固化成结构，并提供可断言的指标：

- 成功率
- 失败分类计数（blocked / rate_limited / captcha / ...）
- JS 扫描覆盖率（discovered vs scanned）
- 耗时

用法::

    report = RunReport.from_outcomes("bilibili", "bilibili", outcomes)
    report.write("runs/20261108-bilibili.json")
    assert report.success_rate >= 0.95
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class RunReport:
    task: str
    platform: str
    started_at: str = field(default_factory=_now)
    finished_at: str = ""
    mode: str = ""
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    stop_reason: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    # ── 指标 ──
    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def ok_count(self) -> int:
        return sum(1 for item in self.outcomes if item.get("ok"))

    @property
    def success_rate(self) -> float:
        return self.ok_count / self.total if self.total else 0.0

    @property
    def failed_by_reason(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for item in self.outcomes:
            if item.get("ok"):
                continue
            reason = item.get("stop_reason") or item.get("risk") or item.get("error") or "unknown"
            counter[str(reason)] += 1
        return dict(counter)

    @property
    def coverage_ratio(self) -> float:
        scanned = int(self.coverage.get("scanned", 0) or 0)
        discovered = int(self.coverage.get("discovered", 0) or 0)
        return scanned / discovered if discovered else 1.0

    # ── 序列化 ──
    def finish(self) -> "RunReport":
        self.finished_at = _now()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "platform": self.platform,
            "started_at": self.started_at,
            "finished_at": self.finished_at or _now(),
            "mode": self.mode,
            "total": self.total,
            "ok": self.ok_count,
            "failed": self.total - self.ok_count,
            "success_rate": round(self.success_rate, 4),
            "failed_by_reason": self.failed_by_reason,
            "coverage": {**self.coverage, "ratio": round(self.coverage_ratio, 4)},
            "stop_reason": self.stop_reason,
            "meta": self.meta,
            "outcomes": self.outcomes,
        }

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    @classmethod
    def from_outcomes(
        cls,
        task: str,
        platform: str,
        outcomes: Iterable[Any],
        *,
        mode: str = "",
        coverage: dict[str, Any] | None = None,
        stop_reason: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "RunReport":
        rows: list[dict[str, Any]] = []
        for item in outcomes:
            if isinstance(item, dict):
                rows.append(dict(item))
            elif hasattr(item, "__dataclass_fields__"):
                rows.append(asdict(item))
            else:
                rows.append({"ok": bool(item)})
        return cls(
            task=task,
            platform=platform,
            mode=mode,
            outcomes=rows,
            coverage=coverage or {},
            stop_reason=stop_reason,
            meta=meta or {},
        ).finish()


def assert_thresholds(
    report: RunReport,
    *,
    min_success_rate: float = 0.95,
    min_coverage: float = 1.0,
) -> list[str]:
    """返回未通过的断言列表；空列表表示全部通过。"""
    failures: list[str] = []
    if report.total == 0:
        failures.append("没有任何 outcome（total=0）")
    elif report.success_rate < min_success_rate:
        failures.append(
            f"成功率 {report.success_rate:.1%} < 要求 {min_success_rate:.1%}"
        )
    if report.coverage and report.coverage_ratio < min_coverage:
        failures.append(
            f"JS 覆盖率 {report.coverage_ratio:.1%} < 要求 {min_coverage:.1%}"
        )
    return failures
