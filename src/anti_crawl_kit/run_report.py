"""可判定的运行报告（run_report.json）。

为什么需要它
------------
``stop_reason`` / 覆盖率如果只写在文档里，就没法在 CI 里判定。
这个模块把一次任务的结果固化成结构，并提供可断言的指标：

- 成功率
- 失败分类计数（blocked / rate_limited / captcha / ...）
- JS 扫描覆盖率（discovered vs scanned）
- 条目覆盖率（expected_count vs actual_count）
- 降级原因与数据源策略
- 耗时

**静默失效红线**：``actual_count == 0`` 一律记为 ``empty``，绝不能算 ``ok``。
实战事故：简书 / 掘金 / 百家号任务 2~4 秒「成功」完成、``item_cnt=0``，
根因是入口参数分支漏了三个平台；外层只看「任务没抛异常」就判成功，
用户投诉时已经晚了好几天。

用法::

    report = RunReport.from_outcomes(
        "bilibili",
        "bilibili",
        outcomes,
        expected_count=20,
        actual_count=18,
        degraded_reasons=["dom_fallback"],
        source_strategy="anonymous_json",
    )
    report.write("runs/20261108-bilibili.json")
    failures = assert_thresholds(report, min_item_coverage=0.9)
    assert not failures, failures
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# stop_reason 命中这些值说明任务被外部因素硬停，不是「跑完了但结果少」。
_BLOCKED_REASONS = frozenset(
    {
        "blocked",
        "captcha",
        "challenge",
        "rate_limited",
        "signature_failed",
        "risk",
        "login_required",
    }
)

# outcome 里可能携带降级信息的字段名（按优先级）。
_DEGRADED_KEYS = ("degraded_reasons", "degraded")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_str_list(value: Any) -> list[str]:
    """把 bool / str / list 统一成去重后的字符串列表。"""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item]
    return [str(value)]


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

    # ── 静默失效四字段（缺一不可）──
    # expected_count: 预期条目数（列表页总数 / 已知应有条数）；None 表示未知。
    # actual_count:   实际拿到的有效条目数；None 表示回退到 ok_count。
    # degraded_reasons: 降级原因（dom_fallback / api_changed / partial_*）。
    # source_strategy:  实际使用的数据源策略（anonymous_json / ssr_state / ...）。
    expected_count: int | None = None
    actual_count: int | None = None
    degraded_reasons: list[str] = field(default_factory=list)
    source_strategy: str = ""

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
    def actual(self) -> int:
        """实际条目数。未显式提供时回退到成功 outcome 数。"""
        if self.actual_count is not None:
            return max(0, int(self.actual_count))
        return self.ok_count

    @property
    def expected(self) -> int | None:
        if self.expected_count is None:
            return None
        return max(0, int(self.expected_count))

    @property
    def item_coverage(self) -> float | None:
        """条目覆盖率；expected 未知时返回 None（不要伪造 1.0）。"""
        expected = self.expected
        if expected is None or expected <= 0:
            return None
        return min(1.0, self.actual / expected)

    @property
    def all_degraded_reasons(self) -> list[str]:
        """显式降级原因 + 从 outcomes 自动收集，去重保序。"""
        reasons: list[str] = []
        for reason in self.degraded_reasons:
            if reason and reason not in reasons:
                reasons.append(str(reason))
        for item in self.outcomes:
            for key in _DEGRADED_KEYS:
                for reason in _as_str_list(item.get(key)):
                    if reason not in reasons:
                        reasons.append(reason)
        return reasons

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

    @property
    def status(self) -> str:
        """任务健康状态：ok / empty / degraded / blocked。

        ``empty`` 是一等公民：拿到 0 条就不是成功，不管任务有没有抛异常。
        """
        if self.stop_reason in _BLOCKED_REASONS:
            return "blocked"
        if self.actual <= 0:
            return "empty"
        reasons = self.all_degraded_reasons
        item_coverage = self.item_coverage
        if reasons or (item_coverage is not None and item_coverage < 1.0):
            return "degraded"
        return "ok"

    @property
    def is_healthy(self) -> bool:
        return self.status == "ok"

    # ── 序列化 ──
    def finish(self) -> "RunReport":
        self.finished_at = _now()
        return self

    def to_dict(self) -> dict[str, Any]:
        item_coverage = self.item_coverage
        return {
            "task": self.task,
            "platform": self.platform,
            "started_at": self.started_at,
            "finished_at": self.finished_at or _now(),
            "mode": self.mode,
            "status": self.status,
            "total": self.total,
            "ok": self.ok_count,
            "failed": self.total - self.ok_count,
            "success_rate": round(self.success_rate, 4),
            "expected_count": self.expected,
            "actual_count": self.actual,
            "item_coverage": None if item_coverage is None else round(item_coverage, 4),
            "degraded_reasons": self.all_degraded_reasons,
            "source_strategy": self.source_strategy,
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
        expected_count: int | None = None,
        actual_count: int | None = None,
        degraded_reasons: Iterable[str] | None = None,
        source_strategy: str = "",
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
            expected_count=expected_count,
            actual_count=actual_count,
            degraded_reasons=list(degraded_reasons or []),
            source_strategy=source_strategy,
        ).finish()


def assert_thresholds(
    report: RunReport,
    *,
    min_success_rate: float = 0.95,
    min_coverage: float = 1.0,
    min_item_coverage: float = 0.9,
    allow_empty: bool = False,
    allow_degraded: bool = False,
) -> list[str]:
    """返回未通过的断言列表；空列表表示全部通过。

    Args:
        min_success_rate: outcome 成功率下限。
        min_coverage: JS 扫描覆盖率下限（``coverage`` 为空时不检查）。
        min_item_coverage: 条目覆盖率下限（``expected_count`` 未知时不检查）。
        allow_empty: 显式允许 0 条结果（默认不允许：0 条 = 静默失效）。
        allow_degraded: 显式允许降级原因（默认不允许）。
    """
    failures: list[str] = []
    if report.total == 0:
        failures.append("没有任何 outcome（total=0）")
    elif report.success_rate < min_success_rate:
        failures.append(
            f"成功率 {report.success_rate:.1%} < 要求 {min_success_rate:.1%}"
        )

    if not allow_empty and report.actual <= 0:
        failures.append(
            "actual_count=0（静默空结果，不能算成功；检查入口参数/选择器/接口）"
        )

    item_coverage = report.item_coverage
    if item_coverage is not None and item_coverage < min_item_coverage:
        failures.append(
            f"条目覆盖率 {item_coverage:.1%} < 要求 {min_item_coverage:.1%}"
            f"（expected={report.expected} actual={report.actual}）"
        )

    reasons = report.all_degraded_reasons
    if reasons and not allow_degraded:
        failures.append(f"存在降级原因: {', '.join(reasons)}")

    if report.coverage and report.coverage_ratio < min_coverage:
        failures.append(
            f"JS 覆盖率 {report.coverage_ratio:.1%} < 要求 {min_coverage:.1%}"
        )
    return failures
