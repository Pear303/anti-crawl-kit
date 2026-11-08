"""稳定性验证 —— 多次运行 / 并发 / 限流/封禁观察（G3）。

`ack-smoke` 验证「一条链路能不能跑通」，本模块验证「连续跑还稳不稳」：

- 重复 N 次，成功率和条目数是否稳定；
- 可选并发，观察 403/429/验证码是否出现；
- 单次失败不掩盖其他样本，输出完整样本便于归因。

用法::

    result = run_stability(
        StabilityCase(url="https://books.toscrape.com/", item_selector="article.product_pod"),
        fetch=my_fetch,       # fetch(url) -> (status, html, error)
    )
    assert result.ok, result.issues
"""

from __future__ import annotations

import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from .smoke import count_in_html

_BLOCK_STATUS = {403, 429, 503}
_BLOCK_MARKERS = (
    "captcha",
    "verify",
    "challenge",
    "访问验证",
    "安全验证",
    "滑动验证",
    "请完成验证",
)


@dataclass(frozen=True)
class StabilityCase:
    name: str = "stability"
    url: str = ""
    item_selector: str = ""
    runs: int = 3
    concurrency: int = 1
    interval_ms: int = 500
    min_success_rate: float = 1.0
    count_tolerance: float = 0.2
    expect_no_block: bool = True


@dataclass
class Sample:
    index: int
    ok: bool
    status: int
    count: int
    latency_ms: int
    blocked: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ok": self.ok,
            "status": self.status,
            "count": self.count,
            "latency_ms": self.latency_ms,
            "blocked": self.blocked,
            "error": self.error,
        }


@dataclass
class StabilityResult:
    name: str
    ok: bool
    runs: int
    success: int
    success_rate: float
    counts: list[int] = field(default_factory=list)
    latencies_ms: list[int] = field(default_factory=list)
    median_count: float = 0.0
    count_stable: bool = True
    blocked: int = 0
    issues: list[str] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "runs": self.runs,
            "success": self.success,
            "success_rate": round(self.success_rate, 4),
            "counts": self.counts,
            "latencies_ms": self.latencies_ms,
            "median_count": self.median_count,
            "count_stable": self.count_stable,
            "blocked": self.blocked,
            "issues": self.issues,
            "samples": [sample.to_dict() for sample in self.samples],
        }


def run_stability(
    case: StabilityCase,
    fetch: Callable[[str], tuple[int, str, str]],
) -> StabilityResult:
    """执行稳定性验证。``fetch`` 返回 ``(status, html, error)``。"""
    runs = max(1, case.runs)
    concurrency = max(1, min(case.concurrency, runs))
    interval = max(0, case.interval_ms) / 1000

    def one(index: int) -> Sample:
        started = time.perf_counter()
        try:
            status, html, error = fetch(case.url)
        except Exception as exc:  # 单次异常不掩盖其他样本
            return Sample(
                index,
                False,
                0,
                0,
                int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
        count = count_in_html(html, case.item_selector) if case.item_selector else 0
        lowered = (html or "").lower()
        blocked = status in _BLOCK_STATUS or any(
            marker in lowered for marker in _BLOCK_MARKERS
        )
        ok = status == 200 and not blocked and (not case.item_selector or count > 0)
        return Sample(
            index,
            ok,
            status,
            count,
            int((time.perf_counter() - started) * 1000),
            blocked=blocked,
            error=error or ("" if ok else f"HTTP {status}"),
        )

    samples: list[Sample] = []
    if concurrency == 1:
        for index in range(runs):
            samples.append(one(index))
            if interval and index < runs - 1:
                time.sleep(interval)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = []
            for index in range(runs):
                futures.append(pool.submit(one, index))
                if interval:
                    time.sleep(interval)
            samples = [future.result() for future in as_completed(futures)]
        samples.sort(key=lambda item: item.index)

    success = sum(1 for sample in samples if sample.ok)
    success_rate = success / len(samples) if samples else 0.0
    counts = [sample.count for sample in samples if sample.ok and case.item_selector]
    median_count = float(statistics.median(counts)) if counts else 0.0
    count_stable = True
    if counts:
        tolerance = max(1.0, median_count * max(0.0, case.count_tolerance))
        count_stable = (max(counts) - min(counts)) <= tolerance

    blocked = sum(1 for sample in samples if sample.blocked)
    issues: list[str] = []
    if success_rate < case.min_success_rate:
        issues.append(
            f"成功率 {success_rate:.1%} < 要求 {case.min_success_rate:.1%}"
        )
    if not count_stable:
        issues.append(f"条目数不稳定: {counts}（中位数 {median_count}）")
    if case.expect_no_block and blocked:
        issues.append(f"出现封禁/限流/验证码 {blocked} 次（403/429/503 或验证页）")

    return StabilityResult(
        name=case.name,
        ok=not issues,
        runs=len(samples),
        success=success,
        success_rate=success_rate,
        counts=counts,
        latencies_ms=[sample.latency_ms for sample in samples],
        median_count=median_count,
        count_stable=count_stable,
        blocked=blocked,
        issues=issues,
        samples=samples,
    )


__all__ = ["Sample", "StabilityCase", "StabilityResult", "run_stability"]
