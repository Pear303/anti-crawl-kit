"""统一等待编排器 —— 验证码 + 登录「双时钟」轮询。

为什么需要双时钟（实战反馈）：
    旧实现把「验证码等待」和「登录等待」塞进同一个总预算，
    验证码占用的每一秒都从登录预算里扣，用户经常来不及扫码。

本编排器：
    - captcha_clock 仅在「检测到验证码」时累计，上限 CAPTCHA_WAIT_SECONDS
    - login_clock   仅在「未登录且无验证码」时累计，上限 LOGIN_WAIT_SECONDS
    两者互不挤占。

平台只需提供两个回调：
    - check_login(page) -> LoginStatus
    - captcha_dom_js（可选，平台验证码 DOM 检测 JS）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from .alive import ensure_alive
from .captcha import detect_captcha_page
from .login import LoginStatus
from .timeouts import (
    CAPTCHA_POLL_SECONDS,
    CAPTCHA_WAIT_SECONDS,
    LOGIN_POLL_SECONDS,
    LOGIN_WAIT_SECONDS,
)

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 15


class WaitReason(StrEnum):
    """固定失败归因优先级（从高到低）。

    实战：抖音曾把「未登录 / 关窗」统一误报成「验证码已等待完成但被风控」，
    排查方向直接被带偏。归因必须按固定优先级，不能按「最后看到的异常」。
    """

    OK = "ok"
    BROWSER_CLOSED = "browser_closed"
    CAPTCHA_NOT_DONE = "captcha_not_done"
    NOT_LOGGED_IN = "not_logged_in"
    EMPTY_AFTER_CAPTCHA = "empty_after_captcha"
    EMPTY_RESULT = "empty_result"


@dataclass(frozen=True)
class WaitOutcome:
    """编排结果。"""
    resolved: bool
    captcha_resolved: bool
    login_ok: bool
    captcha_exhausted: bool
    login_exhausted: bool
    elapsed: float
    last_captcha_reason: str = ""
    last_login_method: str = ""
    reason: str = ""


def classify_wait_outcome(
    outcome: WaitOutcome,
    *,
    item_count: int | None = None,
    browser_closed: bool = False,
) -> WaitReason:
    """按固定优先级归因，避免「关窗/未登录」被误报成风控。

    优先级：
        浏览器关闭 > 人机验证未完成 > 未登录 > 过验证码后仍空 > 一般空结果
    """
    if browser_closed:
        return WaitReason.BROWSER_CLOSED
    if outcome.captcha_exhausted:
        return WaitReason.CAPTCHA_NOT_DONE
    if not outcome.login_ok:
        return WaitReason.NOT_LOGGED_IN
    if item_count == 0:
        if outcome.captcha_resolved and outcome.last_captcha_reason:
            return WaitReason.EMPTY_AFTER_CAPTCHA
        return WaitReason.EMPTY_RESULT
    return WaitReason.OK


def wait_for_captcha_and_login(
    *,
    page: Any,
    check_login: Callable[[Any], LoginStatus],
    captcha_dom_js: str | None = None,
    search_url: str | None = None,
    on_resolved_renavigate: Callable[[Any, str], None] | None = None,
    on_timeout: Callable[[Any], None] | None = None,
    on_state_change: Callable[[str], None] | None = None,
    on_cancel_check: Callable[[], None] | None = None,
    captcha_timeout: int | None = None,
    login_timeout: int | None = None,
    poll: int | None = None,
    label: str = "",
) -> WaitOutcome:
    """双时钟轮询：验证码与登录各自独立预算，交替检测直到两者都通过。

    Args:
        check_login: 平台登录检测，接收 page，返回 LoginStatus。
        captcha_dom_js: 平台验证码 DOM 检测 JS。
        search_url: 恢复后重新导航的目标 URL（可为 None）。
        on_resolved_renavigate: 恢复后的重导航钩子；None 表示不重导航。
        on_timeout: 超时诊断钩子。
        on_state_change: 状态变化调试钩子。
        on_cancel_check: 任务取消检查回调（抛异常即中断）。
        captcha_timeout/login_timeout: 各自预算（默认读 timeouts）。
        poll: 轮询间隔（秒）。
        label: 日志前缀。
    """
    if captcha_timeout is None:
        captcha_timeout = CAPTCHA_WAIT_SECONDS
    if login_timeout is None:
        login_timeout = LOGIN_WAIT_SECONDS
    if poll is None:
        poll = LOGIN_POLL_SECONDS

    t0 = time.time()
    captcha_clock = 0.0
    login_clock = 0.0
    last_state = ""
    last_captcha_reason = ""
    last_login_method = ""
    last_heartbeat = 0.0
    had_interruption = False

    prefix = f"[{label}]" if label else "[WAIT]"
    print(f"\n  {prefix} 采集器已打开浏览器窗口。", flush=True)
    print(f"  {prefix} 如遇验证码请手动完成，如需登录请在窗口中登录。", flush=True)
    print(
        f"  {prefix} 双时钟等待：验证码上限 {captcha_timeout}s，"
        f"登录上限 {login_timeout}s。\n",
        flush=True,
    )

    def _emit(msg: str) -> None:
        if on_state_change:
            try:
                on_state_change(msg)
            except Exception:
                pass

    while True:
        # 0. 取消感知：长等待期间也要能立即退出。
        if on_cancel_check:
            on_cancel_check()

        # 0.5 关窗即取消：死窗口上不要空烧预算。
        ensure_alive(page, label=label)

        # 1. 验证码检测（优先）
        captcha = detect_captcha_page(page, dom_detect_js=captcha_dom_js)
        if captcha.detected:
            last_captcha_reason = captcha.reason
            if captcha_clock == 0.0:
                had_interruption = True
            if last_state != "captcha":
                reason_text = "验证码/滑块" if captcha.reason != "phone_bind_url" else "手机号绑定"
                print(
                    f"  {prefix} [WARN] 当前在{reason_text}页面 (检测: {captcha.reason})，"
                    f"请在浏览器中手动完成...",
                    flush=True,
                )
                _emit(f"CAPTCHA detected reason={captcha.reason}")
                last_state = "captcha"
            if captcha_clock >= captcha_timeout:
                print(
                    f"  {prefix} [WARN] 验证码等待已耗尽 {captcha_timeout}s，放弃验证码等待",
                    flush=True,
                )
                _emit(f"CAPTCHA_EXHAUSTED after {captcha_clock:.0f}s")
                elapsed = time.time() - t0
                if on_timeout:
                    try:
                        on_timeout(page)
                    except Exception:
                        pass
                return WaitOutcome(
                    resolved=False,
                    captcha_resolved=False,
                    login_ok=False,
                    captcha_exhausted=True,
                    login_exhausted=False,
                    elapsed=elapsed,
                    last_captcha_reason=last_captcha_reason,
                    last_login_method=last_login_method,
                    reason=WaitReason.CAPTCHA_NOT_DONE.value,
                )
            time.sleep(poll)
            captcha_clock += poll
            continue

        # 2. 登录态检测
        status = check_login(page)
        if status.is_login:
            last_login_method = status.method or ""
            if had_interruption and search_url and on_resolved_renavigate:
                print(f"  {prefix} [OK] 验证码已通过 + 已登录，重新导航到目标页...", flush=True)
                _emit(f"LOGIN_OK method={last_login_method} (renavigating)")
                try:
                    on_resolved_renavigate(page, search_url)
                except Exception:
                    pass
                post = detect_captcha_page(page, dom_detect_js=captcha_dom_js)
                if post.detected:
                    print(f"  {prefix} [WARN] 导航后又触发验证码，继续等待...", flush=True)
                    _emit(f"POST_NAV_CAPTCHA reason={post.reason}")
                    last_captcha_reason = post.reason
                    last_state = "captcha"
                    time.sleep(poll)
                    captcha_clock += poll
                    continue
            else:
                print(f"  {prefix} [OK] 已登录 (method={last_login_method})，开始采集", flush=True)
                _emit(f"LOGIN_OK method={last_login_method} (no renavigate)")
            return WaitOutcome(
                resolved=True,
                captcha_resolved=True,
                login_ok=True,
                captcha_exhausted=False,
                login_exhausted=False,
                elapsed=time.time() - t0,
                last_captcha_reason=last_captcha_reason,
                last_login_method=last_login_method,
                reason=WaitReason.OK.value,
            )

        # 3. 未登录（无验证码）
        if login_clock == 0.0:
            had_interruption = True
        if login_clock >= login_timeout:
            print(f"  {prefix} [WARN] 登录等待已耗尽 {login_timeout}s", flush=True)
            _emit(f"LOGIN_EXHAUSTED after {login_clock:.0f}s")
            break

        reason = status.reason or "unknown"
        current_state = f"not_login:{reason}"
        if current_state != last_state:
            remaining = int(login_timeout - login_clock)
            print(
                f"  {prefix} [LOGIN] 未登录 (reason={reason})，剩余 {remaining}s，"
                f"请在浏览器中登录...",
                flush=True,
            )
            _emit(f"NOT_LOGIN reason={reason} remaining={remaining}s")
            last_state = current_state

        now = time.time()
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            remaining = int(login_timeout - login_clock)
            print(
                f"  {prefix} [WAIT] 等待登录 ({login_clock:.0f}s/{login_timeout}s，"
                f"剩余 {remaining}s)...",
                flush=True,
            )
            last_heartbeat = now
        time.sleep(poll)
        login_clock += poll

    elapsed = time.time() - t0
    if on_timeout:
        try:
            on_timeout(page)
        except Exception:
            pass
    print(
        f"  {prefix} [WARN] 等待结束（验证码/登录未全部通过，{elapsed:.0f}s），继续尝试采集",
        flush=True,
    )
    return WaitOutcome(
        resolved=False,
        captcha_resolved=False,
        login_ok=False,
        captcha_exhausted=captcha_clock >= captcha_timeout,
        login_exhausted=True,
        elapsed=elapsed,
        last_captcha_reason=last_captcha_reason,
        last_login_method=last_login_method,
        reason=WaitReason.NOT_LOGGED_IN.value,
    )


_STABLE_TEXT_JS = """
(sel) => {
  var el = document.querySelector(sel);
  if (!el) return '';
  return el.innerText || el.textContent || '';
}
"""


def wait_for_stable_text(
    page: Any,
    selector: str,
    *,
    min_chars: int = 80,
    stable_rounds: int = 3,
    timeout: float = 30.0,
    poll: float = 0.8,
    gate: Any = None,
    label: str = "",
    raise_on_timeout: bool = False,
) -> str:
    """等待正文渲染稳定（连续 N 次采样文本不变），返回文本。

    为什么需要它：
        - 加密/异步渲染的页面，`goto` 返回时 DOM 还是骨架屏；
        - 固定 `sleep(5)` 要么不够、要么浪费；
        - 用「文本长度 + 内容连续 stable_rounds 次不变」判定，快页面 1~2s 就返回。

    Args:
        selector: 正文容器选择器（可传逗号组合）。
        min_chars: 少于此长度视为还没渲染完。
        stable_rounds: 连续多少次采样一致才算稳定。
        timeout: 最长等待秒数；超时返回当前文本（默认不抛）。
        gate: 可选 ChallengeGate；每轮采样前检查验证墙。
        raise_on_timeout: True 时超时抛 TimeoutError。
    """
    prefix = f"[{label}]" if label else "[STABLE]"
    deadline = time.time() + max(0.1, timeout)
    last_norm = ""
    stable = 0
    latest = ""

    while time.time() < deadline:
        if gate is not None:
            gate.check_or_stop(page)
        try:
            text = page.evaluate(_STABLE_TEXT_JS, selector) or ""
        except Exception:
            text = ""
        latest = text
        norm = " ".join(str(text).split())
        if len(norm) >= min_chars and norm == last_norm:
            stable += 1
            if stable >= max(1, stable_rounds):
                return text
        else:
            stable = 0
            last_norm = norm
        time.sleep(max(0.1, poll))

    if raise_on_timeout:
        raise TimeoutError(
            f"{prefix} 等待正文稳定超时（{timeout}s，selector={selector!r}，"
            f"当前 {len(latest)} 字符）"
        )
    logger.warning(
        "%s 等待正文稳定超时（%ss，selector=%r，当前 %d 字符），返回当前内容",
        prefix,
        timeout,
        selector,
        len(latest),
    )
    return latest
