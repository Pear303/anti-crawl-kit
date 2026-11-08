"""浏览器存活探测 —— 跨链路共享的「关窗即取消」基础设施。

背景（实战事故）：
    用户在采集进行中手动关闭浏览器窗口后，各等待循环（登录/验证码/结果加载）
    无法感知窗口已死，会空转烧完全部预算（验证码 100s + 登录 200s），
    多关键词场景还会反复重开新窗口。

设计原则：
  - 只有窗口确实已死才抛 BrowserClosedError；非关闭类异常不得误判。
  - BrowserClosedError 继承 RuntimeError，现有 except Exception 兜底照常兼容。

★ 关键教训一（is_closed 假阴性）：
    浏览器进程被杀后 CDP 断连，Playwright 收不到 targetDestroyed 事件，
    page.is_closed() 会返回 False。所以本地属性只能作「确认死亡」的快路径
    （True 即死），存活必须由轻量 evaluate 探针确认（约 1-5ms，可忽略）。

★ 关键教训二（不能收 "destroyed"）：
    patchright 在页面导航期间 evaluate 会抛
    "Execution context was destroyed, most likely because of a navigation"，
    此时窗口还活着。若把 destroyed 当关闭特征，登录成功整页跳转 / 验证码
    通过 re-navigate 时会把活窗口误判为关闭，任务被误杀。
"""
from __future__ import annotations

from typing import Any

BROWSER_CLOSED_MESSAGE = (
    "检测到浏览器窗口已被关闭，请重新运行任务并保持浏览器窗口打开"
)

# 关闭类异常消息特征（小写匹配）。
# 覆盖 TargetClosedError 标准文本与 driver 断连文本；
# 刻意不收 "destroyed" / "connection" / "websocket"（防误判）。
_CLOSED_MARKERS = ("closed", "target")


class BrowserClosedError(RuntimeError):
    """浏览器窗口已被用户手动关闭。"""


def _error_looks_closed(message: str) -> bool:
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _CLOSED_MARKERS)


def is_page_closed(page: Any) -> bool:
    """检测 Playwright 页面/浏览器是否已被关闭（不抛异常）。

    两段式探测：
      1. 快路径：is_closed() 为 True 直接判死（零开销）。
      2. 探针：一次轻量 evaluate 确认存活；抛关闭类异常即判死，
         其他异常（超时等）保守判活、不误杀。
    """
    pw = getattr(page, "_driver", page)
    try:
        if hasattr(pw, "is_closed") and pw.is_closed():
            return True
    except Exception as e:
        return _error_looks_closed(str(e))
    try:
        pw.evaluate("() => 1")
        return False
    except Exception as e:
        return _error_looks_closed(str(e))


def ensure_alive(page: Any, label: str = "") -> None:
    """页面已死则抛 BrowserClosedError，供各等待循环轮询入口调用。"""
    if not is_page_closed(page):
        return
    msg = BROWSER_CLOSED_MESSAGE
    if label:
        msg = f"{msg} [{label}]"
    raise BrowserClosedError(msg)
