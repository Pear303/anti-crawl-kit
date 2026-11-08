"""统一超时配置 —— 可通过环境变量覆盖，方便不同项目调优。

环境变量：
    AC_KIT_CAPTCHA_WAIT   验证码等待超时（秒，默认 100）
    AC_KIT_LOGIN_WAIT     登录扫码等待超时（秒，默认 200）
    AC_KIT_NAV_TIMEOUT    页面导航超时（毫秒，默认 30000）
"""
from __future__ import annotations

import os


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key, "")
    try:
        return int(val) if val.strip() else default
    except ValueError:
        return default


CAPTCHA_WAIT_SECONDS = _env_int("AC_KIT_CAPTCHA_WAIT", 100)
LOGIN_WAIT_SECONDS = _env_int("AC_KIT_LOGIN_WAIT", 200)
NAV_TIMEOUT_MS = _env_int("AC_KIT_NAV_TIMEOUT", 30000)

CAPTCHA_POLL_SECONDS = 3
LOGIN_POLL_SECONDS = 3
