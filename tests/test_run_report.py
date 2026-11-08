"""RunReport 的可判定指标测试（离线）。"""

from __future__ import annotations

import json
from pathlib import Path

from anti_crawl_kit.run_report import RunReport, assert_thresholds


def _outcomes() -> list[dict]:
    return [
        {"ok": True, "elapsed_ms": 100},
        {"ok": True, "elapsed_ms": 120},
        {"ok": False, "stop_reason": "captcha"},
        {"ok": False, "stop_reason": "rate_limited"},
    ]


def test_success_rate() -> None:
    report = RunReport.from_outcomes("t", "demo", _outcomes())
    assert report.total == 4
    assert report.ok_count == 2
    assert report.success_rate == 0.5


def test_failed_by_reason() -> None:
    report = RunReport.from_outcomes("t", "demo", _outcomes())
    assert report.failed_by_reason == {"captcha": 1, "rate_limited": 1}


def test_coverage_ratio() -> None:
    report = RunReport.from_outcomes(
        "t", "demo", _outcomes(), coverage={"discovered": 50, "scanned": 45}
    )
    assert report.coverage_ratio == 0.9
    assert report.to_dict()["coverage"]["ratio"] == 0.9


def test_coverage_defaults_to_full_when_unknown() -> None:
    report = RunReport.from_outcomes("t", "demo", _outcomes())
    assert report.coverage_ratio == 1.0


def test_write_and_read(tmp_path: Path) -> None:
    report = RunReport.from_outcomes("t", "demo", _outcomes())
    out = report.write(tmp_path / "run.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["total"] == 4
    assert data["success_rate"] == 0.5
    assert data["finished_at"]
    assert len(data["outcomes"]) == 4


def test_assert_thresholds_pass() -> None:
    outcomes = [{"ok": True} for _ in range(20)]
    report = RunReport.from_outcomes("t", "demo", outcomes)
    assert assert_thresholds(report, min_success_rate=0.95) == []


def test_assert_thresholds_fail_success_rate() -> None:
    report = RunReport.from_outcomes("t", "demo", _outcomes())
    failures = assert_thresholds(report, min_success_rate=0.95)
    assert any("成功率" in item for item in failures)


def test_assert_thresholds_fail_empty() -> None:
    report = RunReport.from_outcomes("t", "demo", [])
    failures = assert_thresholds(report)
    assert any("total=0" in item for item in failures)


def test_assert_thresholds_fail_coverage() -> None:
    report = RunReport.from_outcomes(
        "t",
        "demo",
        [{"ok": True}],
        coverage={"discovered": 50, "scanned": 40},
    )
    failures = assert_thresholds(report, min_coverage=1.0)
    assert any("覆盖率" in item for item in failures)
