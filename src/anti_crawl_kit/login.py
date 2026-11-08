"""登录态检测公共等待框架。

设计原则：
    - 公共层只提供通用等待框架（轮询、超时、进度通知、关窗感知）。
    - 平台检测规则（cookie/头像/URL/接口）留在各平台适配层，不要写进公共层。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .alive import ensure_alive


@dataclass(frozen=True)
class LoginStatus:
    is_login: bool
    method: str = ""
    reason: str = ""
    uid: str = ""
    username: str = ""
    uncertain: bool = False


def wait_for_login_status(
    check_status: Callable[[Any], LoginStatus],
    page: Any,
    *,
    timeout_seconds: int,
    poll_seconds: int,
    label: str,
) -> LoginStatus:
    """轮询等待登录成功。

    Args:
        check_status: 平台登录检测函数，接收 page，返回 LoginStatus。
        page: Playwright page / PageAdapter。
        timeout_seconds: 最大等待秒数。
        poll_seconds: 轮询间隔。
        label: 日志前缀（平台名）。
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        # 关窗即取消：避免死窗口上空烧登录预算。
        ensure_alive(page, label=label)
        status = check_status(page)
        if status.is_login:
            return status
        remaining = int(deadline - time.time())
        if remaining > 0 and remaining % 30 == 0:
            print(f"  [LOGIN] {label} 未检测到登录态，剩余 {remaining}s...", flush=True)
        time.sleep(poll_seconds)
    return LoginStatus(False, reason="login_timeout")
