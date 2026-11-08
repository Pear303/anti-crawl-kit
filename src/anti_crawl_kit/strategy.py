"""策略降级执行器 —— 统一「多策略提取 + 失败原因记录」的模式。

实战用法：一个列表页通常有三条路可走，按成本从低到高排列：
    1. 页面内嵌 JSON（__INITIAL_STATE__ / SSR 数据）
    2. 直接调 JSON 接口（需要 cookie / 签名）
    3. DOM 解析（最稳但最慢）

    results = run_strategies([
        ("initial_state", lambda: extract_initial_state(page)),
        ("api", lambda: extract_api(page)),
        ("dom", lambda: extract_dom(page)),
    ], stop_on_first_items=True)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class StrategyResult(Generic[T]):
    name: str
    ok: bool
    items: list[T] = field(default_factory=list)
    error: str = ""


def run_strategies(
    strategies: list[tuple[str, Callable[[], list[T]]]],
    *,
    stop_on_first_items: bool = False,
) -> list[StrategyResult[T]]:
    """顺序执行策略列表，返回每条策略的执行结果。

    Args:
        strategies: [(策略名, 执行函数)] 的有序列表。
        stop_on_first_items: 为 True 时，遇到第一条产出 items 的策略即停止。
    """
    results: list[StrategyResult[T]] = []
    for name, fn in strategies:
        try:
            items = fn()
            result = StrategyResult(name=name, ok=True, items=items)
        except Exception as exc:
            result = StrategyResult(name=name, ok=False, error=str(exc))
        results.append(result)
        if stop_on_first_items and result.items:
            break
    return results
