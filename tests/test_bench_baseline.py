"""bench 基线断言的离线测试。"""

from __future__ import annotations

import json
from pathlib import Path

from anti_crawl_kit.tools.bench import (
    BenchReport,
    BenchResult,
    _assert_baseline,
    _group_stats,
    _median,
)


def test_median_odd_and_even() -> None:
    assert _median([300, 100, 200]) == 200
    assert _median([100, 200, 300, 400]) == 250
    assert _median([]) == 0


def test_group_stats() -> None:
    rows = [
        {"site": "a", "tool": "http", "ok": True, "elapsed_ms": 100},
        {"site": "a", "tool": "http", "ok": True, "elapsed_ms": 300},
        {"site": "a", "tool": "http", "ok": False, "elapsed_ms": 200},
    ]
    stats = _group_stats(rows)
    assert stats[("a", "http")]["ok_rate"] == 2 / 3
    assert stats[("a", "http")]["median_ms"] == 200


def _write_baseline(path: Path, results: list[dict]) -> str:
    path.write_text(json.dumps({"results": results}), encoding="utf-8")
    return str(path)


def test_assert_baseline_pass(tmp_path: Path) -> None:
    baseline = _write_baseline(
        tmp_path / "b.json",
        [{"site": "a", "tool": "http", "ok": True, "elapsed_ms": 100}],
    )
    report = BenchReport(
        started_at="",
        env={},
        results=[BenchResult("a", "http", True, 200, 110, 1000, 1)],
    )
    assert _assert_baseline(report, baseline, 0.2) == 0


def test_assert_baseline_fail_success_rate(tmp_path: Path) -> None:
    baseline = _write_baseline(
        tmp_path / "b.json",
        [{"site": "a", "tool": "http", "ok": True, "elapsed_ms": 100}],
    )
    report = BenchReport(
        started_at="",
        env={},
        results=[BenchResult("a", "http", False, 0, 100, 0, 0)],
    )
    assert _assert_baseline(report, baseline, 0.2) == 1


def test_assert_baseline_fail_slow(tmp_path: Path) -> None:
    baseline = _write_baseline(
        tmp_path / "b.json",
        [{"site": "a", "tool": "http", "ok": True, "elapsed_ms": 100}],
    )
    report = BenchReport(
        started_at="",
        env={},
        results=[BenchResult("a", "http", True, 200, 200, 1000, 1)],
    )
    assert _assert_baseline(report, baseline, 0.2) == 1


def test_assert_baseline_missing_file(tmp_path: Path) -> None:
    report = BenchReport(started_at="", env={})
    assert _assert_baseline(report, str(tmp_path / "nope.json"), 0.2) == 1
