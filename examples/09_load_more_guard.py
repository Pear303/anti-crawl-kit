"""示例 09：自动加载更多 + 验证墙硬停。

适用场景：
    - 无限滚动列表（小红书、抖音、知乎、微博）
    - 「加载更多」按钮（CSDN、掘金、简书）
    - 需要在加载过程中守住止损线：一碰验证墙就停，不重试、不硬刚

行为：
    1. 每轮动作前后检查验证墙（URL / 标题 / 可见验证组件 / 平台特征 / 登录态）。
    2. 命中 → 截图 + HTML + meta.json + 通知（控制台/桌面/webhook）→ 抛异常 → 退出码 2。
    3. 连续 N 轮数量不增长 → 正常停止（no_growth）。
    4. 达到最大轮数 → 正常停止（max_rounds）。

运行::

    python examples/09_load_more_guard.py \
        --url "https://目标站/列表页" \
        --profile ./profiles/demo \
        --platform xiaohongshu \
        --login-js examples/platforms/login_check_generic.js \
        --item-selector "section.note-item" \
        --click-selector "text=加载更多" \
        --max-rounds 20 \
        --out output/load_more.json

    # 用企业微信机器人接收「需要人工」通知
    python examples/09_load_more_guard.py ... \
        --webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"

退出码：
    0  正常停止（no_growth / max_rounds）
    2  检测到验证墙/风控墙（已通知，需人工处理）
    3  登录/验证码等待超时
    4  浏览器被关闭
    130 用户 Ctrl+C
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit import (
    BrowserClosedError,
    BrowserSession,
    ChallengeDetected,
    ChallengeGate,
    LoadMoreDriver,
    LoginStatus,
    Notifier,
    get_platform_captcha_dom_js,
    wait_for_captcha_and_login,
)

_EXTRACT_JS = """
(sel) => Array.from(document.querySelectorAll(sel)).map((el, i) => {
  const a = el.querySelector('a[href]');
  return {
    index: i,
    text: (el.innerText || '').trim().slice(0, 800),
    href: a ? a.href : ''
  };
})
"""


def _read_text(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="加载更多 + 验证墙硬停模板")
    parser.add_argument("--url", required=True)
    parser.add_argument("--profile", default="./profiles/demo")
    parser.add_argument("--platform", default="")
    parser.add_argument("--login-js", default="")
    parser.add_argument("--item-selector", required=True, help="列表项选择器")
    parser.add_argument(
        "--click-selector",
        action="append",
        default=[],
        help="「加载更多」按钮选择器，可重复传；不传则滚动",
    )
    parser.add_argument("--scroll-target", default="window", help="window 或容器选择器")
    parser.add_argument("--scroll-step", type=int, default=0, help="0=滚到底，>0=每次滚动像素")
    parser.add_argument("--max-rounds", type=int, default=30)
    parser.add_argument("--idle-rounds", type=int, default=3)
    parser.add_argument("--wait-after", type=float, default=1.2)
    parser.add_argument("--initial-timeout", type=float, default=10.0)
    parser.add_argument("--out", default="output/load_more.json")
    parser.add_argument("--goto-timeout", type=int, default=60_000)
    parser.add_argument("--login-timeout", type=int, default=240)
    parser.add_argument("--captcha-timeout", type=int, default=120)
    parser.add_argument("--webhook", default="")
    parser.add_argument("--snapshot-dir", default="output/challenge")
    parser.add_argument("--no-login-wait", action="store_true")
    parser.add_argument("--no-desktop", action="store_true")
    parser.add_argument("--no-sound", action="store_true")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    login_js = _read_text(args.login_js)
    notifier = Notifier(
        webhook=args.webhook or None,
        desktop=not args.no_desktop,
        sound=not args.no_sound,
    )
    common_gate = dict(
        platform=args.platform,
        notifier=notifier,
        snapshot_dir=args.snapshot_dir,
        stop_on_login=True,
    )
    gate_pre = ChallengeGate(**common_gate, login_js=None)
    gate = ChallengeGate(**common_gate, login_js=login_js)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        with BrowserSession(
            headless=args.headless,
            data_dir=args.profile,
            block_resources=False,
        ) as page:
            # ── 1. 登录阶段（可选）────────────────────────
            if login_js and not args.no_login_wait:
                print("[1/3] 打开页面，等待登录/验证码...", flush=True)
                page.goto(args.url, timeout=args.goto_timeout)
                gate_pre.check_or_stop(page)

                def check_login(p) -> LoginStatus:
                    try:
                        ok = bool(p.evaluate(login_js, isolated_context=False))
                    except Exception as exc:
                        return LoginStatus(False, reason=f"check_error:{exc!r}")
                    return LoginStatus(ok, method="custom-js" if ok else "")

                outcome = wait_for_captcha_and_login(
                    page=page,
                    check_login=check_login,
                    captcha_dom_js=get_platform_captcha_dom_js(args.platform),
                    login_timeout=args.login_timeout,
                    captcha_timeout=args.captcha_timeout,
                    label=args.platform or "login",
                )
                if not outcome.resolved:
                    print(
                        f"[STOP] 登录/验证码等待超时（{outcome.elapsed:.0f}s），退出。",
                        flush=True,
                    )
                    return 3

            # ── 2. 打开列表 + 自动加载 ─────────────────────
            print("[2/3] 打开列表并开始加载...", flush=True)
            page.goto(args.url, timeout=args.goto_timeout)
            gate.check_or_stop(page)

            driver = LoadMoreDriver(
                page,
                item_selector=args.item_selector,
                gate=gate,
                click_selectors=tuple(args.click_selector),
                scroll_target=args.scroll_target,
                scroll_step=args.scroll_step or None,
                max_rounds=args.max_rounds,
                idle_rounds=args.idle_rounds,
                wait_after=args.wait_after,
                initial_timeout=args.initial_timeout,
                raise_on_challenge=True,
                label=args.platform or "list",
            )
            result = driver.run()

            # ── 3. 落盘 ────────────────────────────────────
            print("[3/3] 提取并保存...", flush=True)
            items = page.evaluate(_EXTRACT_JS, args.item_selector) or []
            record = {
                "url": page.evaluate("() => window.location.href"),
                "title": page.title(),
                "platform": args.platform,
                "collected_at": datetime.now().isoformat(timespec="seconds"),
                "item_selector": args.item_selector,
                "rounds": result.rounds,
                "item_count": result.item_count,
                "stopped_reason": result.stopped_reason,
                "elapsed": round(result.elapsed, 2),
                "items": items,
            }
            out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

            print(
                f"[OK] {result.item_count} 条 | {result.rounds} 轮 | "
                f"停止原因: {result.stopped_reason} | 耗时 {result.elapsed:.1f}s | 输出 {out}",
                flush=True,
            )
            if result.stopped_reason == "cancelled":
                return 130
            return 0

    except ChallengeDetected as exc:
        print("\n[STOP] 检测到人机验证/风控墙，已停止（不会重试）。", flush=True)
        print(
            json.dumps(exc.info.to_dict(), ensure_ascii=False, indent=2),
            flush=True,
        )
        print(
            "\n下一步：手动打开浏览器完成验证，确认账号正常后再重新运行；"
            " 不要立即重跑，否则会加深风控评分。",
            flush=True,
        )
        return 2
    except BrowserClosedError:
        print("\n[STOP] 浏览器已被关闭，退出。", flush=True)
        return 4
    except KeyboardInterrupt:
        print("\n[STOP] 用户中断。", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
