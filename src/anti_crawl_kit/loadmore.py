"""自动加载更多 —— 滚动/点「加载更多」，并在验证墙出现时硬停。

适用：
    - 无限滚动列表（小红书、抖音、知乎、微博）
    - 「加载更多」按钮（CSDN、掘金、简书）
    - 需要滚动触发懒加载的详情页

关键设计：
    - 每一轮动作前后都过 `ChallengeGate`，命中就抛 `ChallengeDetected`。
    - 连续 N 轮数量不增长就停（防止无限滚动/死循环）。
    - 不吞异常：调用方拿到 ChallengeDetected 后应直接退出，不要重试。
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .challenge import ChallengeDetected, ChallengeGate
from .notify import ChallengeInfo

logger = logging.getLogger(__name__)


@dataclass
class LoadMoreResult:
    """加载结果。stopped_reason: no_growth / max_rounds / challenge / cancelled。"""

    rounds: int
    item_count: int
    stopped_reason: str
    challenge: ChallengeInfo | None = None
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.stopped_reason in ("no_growth", "max_rounds")


_SCROLL_JS = """
(arg) => {
  var step = arg.step || 0;
  if (arg.target === 'window' || arg.target === 'document') {
    if (step > 0) window.scrollBy(0, step);
    else window.scrollTo(0, document.body.scrollHeight);
    return true;
  }
  var el = document.querySelector(arg.target);
  if (!el) return false;
  if (step > 0) el.scrollTop += step;
  else el.scrollTop = el.scrollHeight;
  return true;
}
"""

_COUNT_JS = "(sel) => document.querySelectorAll(sel).length"


class LoadMoreDriver:
    """滚动/点击加载更多，带验证墙闸门和「无增长」终止条件。

    用法::

        driver = LoadMoreDriver(
            page,
            item_selector="section.note-item",
            gate=gate,
            click_selectors=("text=加载更多",),
            max_rounds=20,
        )
        result = driver.run()
    """

    def __init__(
        self,
        page: Any,
        *,
        item_selector: str,
        gate: ChallengeGate | None = None,
        click_selectors: tuple[str, ...] | list[str] = (),
        scroll_target: str = "window",
        scroll_step: int | None = None,
        max_rounds: int = 30,
        idle_rounds: int = 3,
        delay: tuple[float, float] = (0.8, 2.0),
        wait_after: float = 1.2,
        initial_timeout: float = 10.0,
        raise_on_challenge: bool = True,
        on_round: Callable[[int, int, bool], None] | None = None,
        label: str = "",
    ):
        if not item_selector:
            raise ValueError("item_selector 不能为空")
        self.page = page
        self.item_selector = item_selector
        self.gate = gate
        self.click_selectors = tuple(click_selectors)
        self.scroll_target = scroll_target
        self.scroll_step = scroll_step
        self.max_rounds = max(1, int(max_rounds))
        self.idle_rounds = max(1, int(idle_rounds))
        self.delay = delay
        self.wait_after = max(0.0, float(wait_after))
        self.initial_timeout = max(0.0, float(initial_timeout))
        self.raise_on_challenge = raise_on_challenge
        self.on_round = on_round
        self.label = label
        self._prefix = f"[{label}]" if label else "[LOAD]"

    # ── 对外入口 ────────────────────────────────────
    def run(self) -> LoadMoreResult:
        t0 = time.time()
        rounds_done = 0
        last = 0
        idle = 0

        if self.initial_timeout > 0:
            try:
                self.page.wait_for_selector(
                    self.item_selector, timeout=int(self.initial_timeout * 1000)
                )
            except Exception:
                logger.warning("%s 首屏未等到 %s，继续尝试滚动", self._prefix, self.item_selector)

        try:
            last = self.count()
            for round_no in range(1, self.max_rounds + 1):
                rounds_done = round_no

                # 动作前：验证墙闸门
                if self.gate is not None:
                    self.gate.check_or_stop(self.page)

                clicked = self._act()

                if self.wait_after > 0:
                    self.page.wait_for_timeout(int(self.wait_after * 1000))

                # 动作后：再查一次（很多验证码是点击后弹出的）
                if self.gate is not None:
                    self.gate.check_or_stop(self.page)

                now = self.count()
                if self.on_round is not None:
                    try:
                        self.on_round(round_no, now, clicked)
                    except Exception:
                        logger.debug("%s on_round 回调异常", self._prefix, exc_info=True)

                logger.info(
                    "%s 第 %d 轮：%d 条（%+d）%s",
                    self._prefix,
                    round_no,
                    now,
                    now - last,
                    " [click]" if clicked else "",
                )

                if now <= last:
                    idle += 1
                    if idle >= self.idle_rounds:
                        return LoadMoreResult(
                            rounds=rounds_done,
                            item_count=now,
                            stopped_reason="no_growth",
                            elapsed=time.time() - t0,
                        )
                else:
                    idle = 0
                    last = now

                if self.delay[1] > 0:
                    time.sleep(random.uniform(*self.delay))

            return LoadMoreResult(
                rounds=rounds_done,
                item_count=last,
                stopped_reason="max_rounds",
                elapsed=time.time() - t0,
            )

        except ChallengeDetected as exc:
            if self.raise_on_challenge:
                raise
            return LoadMoreResult(
                rounds=rounds_done,
                item_count=self._safe_count(last),
                stopped_reason="challenge",
                challenge=exc.info,
                elapsed=time.time() - t0,
            )
        except KeyboardInterrupt:
            return LoadMoreResult(
                rounds=rounds_done,
                item_count=self._safe_count(last),
                stopped_reason="cancelled",
                elapsed=time.time() - t0,
            )

    # ── 动作 ────────────────────────────────────────
    def count(self) -> int:
        try:
            value = self.page.evaluate(_COUNT_JS, self.item_selector)
            return int(value or 0)
        except Exception as exc:
            logger.debug("%s 计数失败: %r", self._prefix, exc)
            return 0

    def _act(self) -> bool:
        """优先点「加载更多」，点不到就滚动。返回是否点击成功。"""
        if self.click_selectors:
            if self._click_more():
                return True
        self._scroll()
        return False

    def _click_more(self) -> bool:
        for sel in self.click_selectors:
            try:
                loc = self.page.locator(sel)
                if loc.count() == 0:
                    continue
                first = loc.first
                if not first.is_visible():
                    continue
                first.click(timeout=2000)
                return True
            except Exception:
                continue
        return False

    def _scroll(self) -> None:
        try:
            self.page.evaluate(
                _SCROLL_JS,
                {"target": self.scroll_target, "step": self.scroll_step or 0},
            )
        except Exception as exc:
            logger.debug("%s 滚动失败: %r", self._prefix, exc)

    def _safe_count(self, fallback: int) -> int:
        try:
            return self.count()
        except Exception:
            return fallback


def load_until_stable(
    page: Any,
    *,
    item_selector: str,
    gate: ChallengeGate | None = None,
    max_rounds: int = 30,
    idle_rounds: int = 3,
    click_selectors: tuple[str, ...] | list[str] = (),
    label: str = "",
) -> LoadMoreResult:
    """一行式封装：滚动到底，连续 idle_rounds 轮不增长就停。"""
    return LoadMoreDriver(
        page,
        item_selector=item_selector,
        gate=gate,
        click_selectors=click_selectors,
        max_rounds=max_rounds,
        idle_rounds=idle_rounds,
        label=label,
    ).run()
