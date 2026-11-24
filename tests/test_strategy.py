"""策略降级执行器的离线测试。"""

from __future__ import annotations

from anti_crawl_kit.strategy import StrategyResult, run_strategies


def test_records_success_and_failure_in_order() -> None:
    def boom() -> list[int]:
        raise RuntimeError("接口 403")

    results = run_strategies([("api", boom), ("dom", lambda: [1, 2])])

    assert [r.name for r in results] == ["api", "dom"]
    assert results[0].ok is False
    assert results[0].items == []
    assert "403" in results[0].error
    assert results[1].ok is True
    assert results[1].items == [1, 2]
    assert results[1].error == ""


def test_runs_all_strategies_by_default() -> None:
    calls: list[str] = []

    def make(name: str, items: list[int]):
        def run() -> list[int]:
            calls.append(name)
            return items

        return run

    results = run_strategies([("a", make("a", [1])), ("b", make("b", [2]))])

    assert calls == ["a", "b"]
    assert len(results) == 2


def test_stop_on_first_items_breaks_after_non_empty() -> None:
    calls: list[str] = []

    def make(name: str, items: list[int]):
        def run() -> list[int]:
            calls.append(name)
            return items

        return run

    results = run_strategies(
        [("empty", make("empty", [])), ("hit", make("hit", [1])), ("never", make("never", [2]))],
        stop_on_first_items=True,
    )

    assert calls == ["empty", "hit"]
    assert [r.name for r in results] == ["empty", "hit"]


def test_stop_on_first_items_ignores_failures() -> None:
    def boom() -> list[int]:
        raise ValueError("x")

    results = run_strategies(
        [("boom", boom), ("hit", lambda: [1])],
        stop_on_first_items=True,
    )

    assert [r.name for r in results] == ["boom", "hit"]
    assert results[1].ok is True


def test_strategy_result_is_frozen() -> None:
    result = StrategyResult(name="a", ok=True, items=[1])
    assert result.error == ""
    try:
        result.name = "b"  # type: ignore[misc]
    except Exception as exc:
        assert "frozen" in str(exc).lower() or "cannot" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("StrategyResult 应该是不可变的")
