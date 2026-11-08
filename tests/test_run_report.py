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


# ── v0.3.0：静默失效四字段 ────────────────────────────
def test_actual_zero_is_empty_not_ok() -> None:
    report = RunReport.from_outcomes(
        "t", "demo", [{"ok": True}], actual_count=0
    )
    assert report.status == "empty"
    assert report.to_dict()["status"] == "empty"
    assert report.is_healthy is False


def test_actual_defaults_to_ok_count() -> None:
    report = RunReport.from_outcomes("t", "demo", _outcomes())
    assert report.actual == 2
    assert report.expected is None
    assert report.item_coverage is None


def test_item_coverage_and_degraded_status() -> None:
    report = RunReport.from_outcomes(
        "t",
        "demo",
        [{"ok": True}, {"ok": True}],
        expected_count=10,
        actual_count=8,
        source_strategy="anonymous_json",
    )
    assert report.item_coverage == 0.8
    assert report.status == "degraded"
    data = report.to_dict()
    assert data["expected_count"] == 10
    assert data["actual_count"] == 8
    assert data["item_coverage"] == 0.8
    assert data["source_strategy"] == "anonymous_json"


def test_degraded_reasons_collected_from_outcomes() -> None:
    report = RunReport.from_outcomes(
        "t",
        "demo",
        [
            {"ok": True, "degraded": "dom_fallback"},
            {"ok": True, "degraded_reasons": ["api_changed", "dom_fallback"]},
        ],
        degraded_reasons=["partial_page"],
    )
    assert report.all_degraded_reasons == [
        "partial_page",
        "dom_fallback",
        "api_changed",
    ]
    assert report.status == "degraded"


def test_blocked_stop_reason_wins() -> None:
    report = RunReport.from_outcomes(
        "t", "demo", [{"ok": True}], stop_reason="captcha"
    )
    assert report.status == "blocked"


def test_assert_thresholds_fail_zero_items() -> None:
    report = RunReport.from_outcomes(
        "t", "demo", [{"ok": True}], actual_count=0
    )
    failures = assert_thresholds(report)
    assert any("actual_count=0" in item for item in failures)


def test_assert_thresholds_allow_empty() -> None:
    report = RunReport.from_outcomes(
        "t", "demo", [{"ok": True}], actual_count=0
    )
    assert assert_thresholds(report, allow_empty=True) == []


def test_assert_thresholds_fail_item_coverage() -> None:
    report = RunReport.from_outcomes(
        "t",
        "demo",
        [{"ok": True}],
        expected_count=10,
        actual_count=8,
    )
    failures = assert_thresholds(report, min_item_coverage=0.9)
    assert any("条目覆盖率" in item for item in failures)


def test_assert_thresholds_fail_degraded() -> None:
    report = RunReport.from_outcomes(
        "t", "demo", [{"ok": True}], degraded_reasons=["dom_fallback"]
    )
    failures = assert_thresholds(report)
    assert any("降级原因" in item for item in failures)
    assert assert_thresholds(report, allow_degraded=True) == []
