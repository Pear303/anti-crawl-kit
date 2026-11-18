"""基线对比：状态退化、指标下降、新增/移除。"""

from __future__ import annotations

from anti_crawl_kit.report_diff import diff_reports


def _health(fail: bool = False) -> dict:
    status = "FAIL" if fail else "OK"
    return {
        "finished_at": "2026-11-13 09:00:00",
        "summary": {"OK": 0 if fail else 1, "WARN": 0, "FAIL": 1 if fail else 0, "SKIP": 0},
        "results": [
            {"dimension": "registry", "platform": "a", "status": status},
        ],
    }


def _smoke(status: str) -> dict:
    return {
        "finished_at": "2026-11-13 08:00:00",
        "summary": {"PASS": 1 if status == "PASS" else 0, "FAIL": 1 if status == "FAIL" else 0, "SKIP": 0},
        "results": [{"name": "books", "kind": "http", "status": status}],
    }


def _run(actual: int) -> dict:
    return {
        "finished_at": "2026-11-13 07:00:00",
        "status": "done" if actual else "empty",
        "expected_count": 20,
        "actual_count": actual,
        "success_rate": 1.0 if actual else 0.0,
        "outcomes": [{"path": "feed", "ok": bool(actual)}],
    }


def _recipe(status: str) -> dict:
    return {
        "finished_at": "2026-11-13 06:00:00",
        "recipe_id": "demo",
        "status": "done" if status == "ok" else "failed",
        "steps": [{"id": "fetch", "status": status, "elapsed_ms": 10}],
    }


def test_health_regression():
    diff = diff_reports(_health(fail=True), _health(fail=False), "health")
    assert diff["regressions"] == 1
    assert diff["improvements"] == 0
    assert diff["items"][0]["change"] == "regressed"
    assert diff["items"][0]["before"] == "OK"
    assert diff["items"][0]["after"] == "FAIL"


def test_health_improvement():
    diff = diff_reports(_health(fail=False), _health(fail=True), "health")
    assert diff["regressions"] == 0
    assert diff["improvements"] == 1


def test_smoke_removed_case_is_regression():
    current = _smoke("PASS")
    current["results"] = []
    diff = diff_reports(current, _smoke("PASS"), "smoke")
    assert diff["removed"] == 1
    assert diff["regressions"] == 1
    assert diff["items"][0]["change"] == "removed"


def test_smoke_added_case_is_counted():
    current = _smoke("PASS")
    current["results"].append({"name": "quotes", "kind": "browser", "status": "PASS"})
    diff = diff_reports(current, _smoke("PASS"), "smoke")
    assert diff["added"] == 1
    assert diff["regressions"] == 0


def test_run_actual_count_drop_is_regression():
    diff = diff_reports(_run(0), _run(20), "run")
    assert diff["regressions"] >= 1
    metric = next(row for row in diff["metrics"] if row["key"] == "actual_count")
    assert metric["change"] == "regressed"


def test_recipe_step_regression():
    diff = diff_reports(_recipe("failed"), _recipe("ok"), "recipe")
    assert diff["regressions"] == 1
    assert diff["items"][0]["change"] == "regressed"


def test_skip_is_neutral():
    current = _smoke("SKIP")
    baseline = _smoke("FAIL")
    diff = diff_reports(current, baseline, "smoke")
    assert diff["regressions"] == 0
    assert diff["items"][0]["change"] == "changed"
