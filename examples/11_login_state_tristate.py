"""示例 11：登录态三态 —— valid / expired / missing。

为什么不能只用 bool：
    cookie 存在 ≠ 登录有效。
    - 过期 cookie 在 Playwright 内存里可能直接「消失」（表现为 missing）；
    - 在 Chromium Cookies SQLite 里能读到 expires_utc，才能区分「过期」和「从未登录」。

三态：

    missing  → 从未登录 / cookie 全被清掉
    expired  → 有认证 cookie，但已过期
    valid    → 有未过期的认证 cookie

本示例分两部分：
    1. 离线演示：用模拟 cookie 走三态判定 + 身份比对 + 阻断决策（一定可跑）
    2. 真实读取：--live 时打开浏览器，读 profile 里的真实 cookie

运行::

    python examples/11_login_state_tristate.py
    python examples/11_login_state_tristate.py --live \\
        --url "https://www.bilibili.com" \\
        --profile ./profiles/bilibili \\
        --auth-cookie SESSDATA
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.platform_spec import (
    IdentityState,
    check_identity,
    eval_cookie_state,
    should_block_task,
)

AUTH_COOKIE = "SESSDATA"


def demo_offline() -> None:
    now = time.time()
    cases = [
        ("valid", [{"name": AUTH_COOKIE, "value": "x", "expires": now + 3600}]),
        ("expired", [{"name": AUTH_COOKIE, "value": "x", "expires": now - 3600}]),
        ("missing", []),
    ]

    print("=== 登录态三态 ===")
    for label, cookies in cases:
        state, reason = eval_cookie_state(cookies, (AUTH_COOKIE,), now=now)
        print(f"{label:8s} -> {state.value:8s} ({reason})")

        for is_publish in (False, True):
            block, message = should_block_task(
                state, IdentityState.UNKNOWN, is_publish=is_publish
            )
            task = "发布" if is_publish else "采集"
            print(f"    {task}: block={block}  {message}")

    print("\n=== 身份比对 ===")
    for actual, expected in [("mid:123", "123"), ("123", "456"), ("", "123")]:
        result = check_identity(actual, expected)
        print(f"  {actual!r:12s} vs {expected!r:8s} -> {result.value}")


def demo_live(url: str, profile: str, auth_cookie: str) -> None:
    from anti_crawl_kit import BrowserSession

    print(f"\n=== 读取真实 cookie（{url}）===")
    with BrowserSession(headless=True, data_dir=profile) as page:
        page.goto(url, timeout=60000)
        cookies = page.context.cookies()

    state, reason = eval_cookie_state(cookies, (auth_cookie,))
    print(f"总数: {len(cookies)}  |  {auth_cookie}: {state.value} ({reason})")

    matched = [
        c for c in cookies if c.get("name") == auth_cookie and c.get("value")
    ]
    if matched:
        expires = matched[0].get("expires", -1)
        print(f"expires={expires}（-1 表示会话 cookie）")

    block, message = should_block_task(state, IdentityState.UNKNOWN, is_publish=False)
    print(f"采集决策: block={block}  {message}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="读取真实 profile cookie")
    parser.add_argument("--url", default="https://www.bilibili.com")
    parser.add_argument("--profile", default="./profiles/demo")
    parser.add_argument("--auth-cookie", default=AUTH_COOKIE)
    args = parser.parse_args()

    demo_offline()
    if args.live:
        demo_live(args.url, args.profile, args.auth_cookie)
    else:
        print("\n（加 --live 读取真实 profile 的 cookie）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
