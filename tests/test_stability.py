"""稳定性验证测试（离线，用替身 fetch）。"""

from __future__ import annotations

import threading

from anti_crawl_kit.stability import StabilityCase, run_stability

HTML = '<div class="item"></div>' * 20


class ScriptedFetch:
    def __init__(self, responses: list[tuple[int, str, str]]) -> None:
        self.responses = list(responses)
        self._lock = threading.Lock()

    def __call__(self, url: str) -> tuple[int, str, str]:
        with self._lock:
            if self.responses:
                return self.responses.pop(0)
            return 200, HTML, ""


def _case(**overrides) -> StabilityCase:
    data = dict(
        name="demo",
        url="https://example.com/",
        item_selector="div.item",
        runs=3,
        interval_ms=0,
    )
    data.update(overrides)
    return StabilityCase(**data)


def test_stability_pass() -> None:
    fetch = ScriptedFetch([(200, HTML, "")] * 3)
    result = run_stability(_case(), fetch)
    assert result.ok
    assert result.counts == [20, 20, 20]
    assert result.blocked == 0


def test_stability_success_rate_fail() -> None:
    fetch = ScriptedFetch(
        [(200, HTML, ""), (500, "", "boom"), (200, HTML, "")]
    )
    result = run_stability(_case(min_success_rate=1.0), fetch)
    assert not result.ok
    assert result.success == 2
    assert any("成功率" in issue for issue in result.issues)


def test_stability_count_unstable() -> None:
    fetch = ScriptedFetch(
        [
            (200, '<div class="item"></div>' * 20, ""),
            (200, '<div class="item"></div>' * 5, ""),
            (200, '<div class="item"></div>' * 20, ""),
        ]
    )
    result = run_stability(_case(count_tolerance=0.1), fetch)
    assert not result.ok
    assert result.count_stable is False


def test_stability_blocked() -> None:
    fetch = ScriptedFetch(
        [(200, HTML, ""), (429, "", "too many"), (200, HTML, "")]
    )
    result = run_stability(_case(min_success_rate=0.5), fetch)
    assert not result.ok
    assert result.blocked == 1
    assert any("封禁" in issue for issue in result.issues)


def test_stability_concurrency() -> None:
    fetch = ScriptedFetch([(200, HTML, "")] * 4)
    result = run_stability(_case(runs=4, concurrency=2), fetch)
    assert result.ok
    assert result.runs == 4
    assert len(result.samples) == 4


def test_stability_to_dict() -> None:
    fetch = ScriptedFetch([(200, HTML, "")] * 2)
    result = run_stability(_case(runs=2), fetch)
    data = result.to_dict()
    assert data["ok"] is True
    assert data["runs"] == 2
    assert len(data["samples"]) == 2
