"""人类行为模拟 —— 降低机械节奏特征。

局限说明（务必理解）：
    page.mouse.move / keyboard.type 是 CDP 合成输入，检测方可以通过
    isTrusted 等特征识别。本模块的价值是「降低机械节奏特征」，
    不承诺通过行为级检测。真正的反检测主力是 patchright + 真实浏览器环境。

用法::

    from anti_crawl_kit.behavior import human_scroll, human_type, random_sleep
"""
from __future__ import annotations

import random
import time
from typing import Any


def _bezier_points(
    p0: tuple[float, float], p1: tuple[float, float], steps: int = 12
) -> list[tuple[float, float]]:
    """生成从 p0 到 p1 的二次贝塞尔曲线采样点（随机控制点）。"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    cx = p0[0] + dx * random.uniform(0.15, 0.35)
    cy = p0[1] + dy * random.uniform(0.15, 0.35) + random.uniform(-30, 30)
    points = []
    for i in range(steps):
        t = i / (steps - 1)
        inv = 1 - t
        x = inv * inv * p0[0] + 2 * inv * t * cx + t * t * p1[0]
        y = inv * inv * p0[1] + 2 * inv * t * cy + t * t * p1[1]
        points.append((round(x, 1), round(y, 1)))
    return points


def _bezier_move(page: Any, x: float, y: float) -> None:
    """按贝塞尔曲线分步移动鼠标到 (x, y)，总时长约 300-700ms。"""
    if not (hasattr(page, "mouse") and hasattr(page.mouse, "move")):
        return
    try:
        viewport = page.viewport_size or {"width": 1920, "height": 1080}
    except Exception:
        viewport = {"width": 1920, "height": 1080}
    start = getattr(page, "_ac_kit_last_mouse", None)
    if start is None:
        start = (
            random.uniform(0, viewport.get("width", 1920)),
            random.uniform(0, viewport.get("height", 1080)),
        )
    for px, py in _bezier_points(start, (x, y), steps=random.randint(10, 16)):
        page.mouse.move(px, py)
        time.sleep(random.uniform(0.02, 0.06))
    try:
        page._ac_kit_last_mouse = (x, y)
    except Exception:
        pass


def random_sleep(min_sec: float = 2.0, max_sec: float = 5.0) -> None:
    """随机延迟，模拟人类操作间隔。"""
    time.sleep(random.uniform(min_sec, max_sec))


def human_jitter(base: float, spread: float = 0.5) -> float:
    """在基础延时上叠加随机抖动。"""
    return base * (1 + random.uniform(-spread, spread))


def human_delay(base_seconds: float = 1.0, jitter_ratio: float = 0.3) -> None:
    """按人类节奏延时，带随机抖动。"""
    jitter = base_seconds * jitter_ratio
    time.sleep(max(0.0, random.uniform(base_seconds - jitter, base_seconds + jitter)))


def human_scroll(
    page: Any,
    *,
    min_steps: int = 2,
    max_steps: int = 4,
    min_delta: int = 300,
    max_delta: int = 800,
    rollback_chance: float = 0.3,
) -> None:
    """模拟人类滚轮：加速→巡航→减速节奏 + 概率回滚。

    如果 mouse.wheel 失败，自动回退到 JS smooth scroll。
    """
    try:
        steps = random.randint(min_steps, max_steps)
        deltas: list[int] = []
        for i in range(steps):
            progress = i / max(1, steps - 1)
            curve = 1 - abs(progress - 0.5) * 1.4
            base = random.randint(min_delta, max_delta)
            deltas.append(max(60, int(base * curve)))
        for d in deltas:
            page.mouse.wheel(0, d)
            time.sleep(random.uniform(0.3, 1.0))
        if random.random() < rollback_chance:
            page.mouse.wheel(0, -random.randint(50, 200))
            time.sleep(random.uniform(0.5, 1.5))
    except Exception:
        try:
            page.evaluate(
                "window.scrollBy({ top: window.innerHeight * 0.8, behavior: 'smooth' })"
            )
        except Exception:
            pass


def human_click(page: Any, selector: str) -> None:
    """带随机偏移的点击（点击前按贝塞尔曲线移动鼠标）。"""
    locator = page.locator(selector).first
    box = locator.bounding_box()
    if box:
        x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
        _bezier_move(page, x, y)
        page.mouse.click(x, y)
    else:
        locator.click(force=True)
    human_delay(0.5, 0.5)


def human_click_locator(locator: Any) -> bool:
    """对 locator 做带随机偏移的点击。返回成功与否。"""
    try:
        box = locator.bounding_box()
        if box:
            x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
            y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
            _bezier_move(locator.page, x, y)
            locator.page.mouse.click(x, y)
            human_delay(0.5, 0.5)
            return True
        locator.click(force=True)
        return True
    except Exception:
        return False


def human_type(page: Any, selector: str, text: str) -> None:
    """逐字符变速键入（50-150ms/字符），5% 思考停顿，3% 错别字自纠。

    错字模拟仅对 ASCII 字母生效；中文走 insertText 直接上屏。
    """
    locator = page.locator(selector).first
    locator.click()
    human_delay(0.5, 0.5)
    for char in text:
        page.keyboard.type(char, delay=random.randint(50, 150))
        if random.random() < 0.05:
            time.sleep(random.uniform(0.3, 0.8))
        if random.random() < 0.03 and char.isascii() and char.isalpha():
            wrong = (
                char.swapcase()
                if random.random() < 0.5
                else chr(ord(char) + random.choice([1, -1]))
            )
            page.keyboard.type(wrong, delay=random.randint(50, 150))
            time.sleep(random.uniform(0.2, 0.5))
            page.keyboard.press("Backspace")
            time.sleep(random.uniform(0.15, 0.4))
            page.keyboard.type(char, delay=random.randint(50, 150))
    if len(text) > 300:
        time.sleep(random.uniform(1.0, 3.0))
