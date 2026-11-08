"""示例 08：登录后的「加密页面」——不逆算法，让浏览器自己解密。

适用场景：
    - 页面内容由 JS 解密后再渲染（接口签名 / 响应加密 / 字体混淆 / 图片分片）
    - 必须登录后才能看到列表或详情
    - 不想花几天逆签名，只想稳定拿到渲染后的数据

核心思路（按优先级）：
    1. 持久化 profile + 人工扫码登录一次，之后复用登录态。
    2. 优先拦截页面自己发的 XHR（响应已经是解密后的 JSON）。
    3. 其次读 SSR 全局状态（__INITIAL_STATE__ / __NEXT_DATA__ / __NUXT__）。
    4. 最后读渲染后的 DOM 文本；用「文本连续 N 次不变」判断渲染完成，而不是固定 sleep。
    5. 全程挂 ChallengeGate：登录失效/验证墙 → 截图 + 通知 + 退出，不重试。

运行::

    # 第一次：有头登录（扫码/输密码），登录态写入 profiles/demo
    python examples/08_logged_in_encrypted.py \
        --url "https://目标站/需要登录的页面" \
        --profile ./profiles/demo \
        --platform xiaohongshu \
        --login-js examples/platforms/login_check_generic.js \
        --content-selector "article, .note-content, #js_content" \
        --api-pattern "/api/" \
        --out output/encrypted_page.json

    # 之后：登录态还在，可以无头跑
    python examples/08_logged_in_encrypted.py ... --headless --no-login-wait

退出码：
    0  成功
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
    LoginStatus,
    Notifier,
    get_platform_captcha_dom_js,
    wait_for_captcha_and_login,
    wait_for_stable_text,
)


def _read_text(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8")


def _extract_state(page, key: str):
    """读取页面主世界的全局状态（必须在主世界执行）。"""
    js = """
    (k) => {
      try {
        var v = window[k];
        if (v === undefined) return null;
        return JSON.parse(JSON.stringify(v));
      } catch (e) {
        return {__error__: String(e)};
      }
    }
    """
    try:
        return page.evaluate(js, key, isolated_context=False)
    except Exception as exc:
        return {"__error__": repr(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="登录后加密页面采集模板")
    parser.add_argument("--url", required=True)
    parser.add_argument("--profile", default="./profiles/demo")
    parser.add_argument("--platform", default="", help="用于平台验证码 DOM 检测，如 xiaohongshu")
    parser.add_argument("--login-js", default="", help="返回 true=已登录 的 JS 文件")
    parser.add_argument("--content-selector", default="article, main, #js_content, .article-content")
    parser.add_argument("--api-pattern", default="", help="要拦截的接口 URL 子串，如 /api/")
    parser.add_argument("--state-key", default="", help="SSR 全局状态变量名，如 __INITIAL_STATE__")
    parser.add_argument("--out", default="output/encrypted_page.json")
    parser.add_argument("--max-html", type=int, default=300_000)
    parser.add_argument("--min-chars", type=int, default=80)
    parser.add_argument("--stable-rounds", type=int, default=3)
    parser.add_argument("--stable-timeout", type=float, default=30.0)
    parser.add_argument("--goto-timeout", type=int, default=60_000)
    parser.add_argument("--login-timeout", type=int, default=240)
    parser.add_argument("--captcha-timeout", type=int, default=120)
    parser.add_argument("--webhook", default="", help="企业微信/飞书/钉钉/Slack webhook")
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
            block_resources=False,  # 登录/验证码阶段必须放行图片
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
            elif login_js:
                print("[1/3] 跳过登录等待，直接使用已有 profile。", flush=True)
            else:
                print("[1/3] 未提供 --login-js，跳过登录检测；若页面要求登录请手动处理。", flush=True)

            # ── 2. 打开目标页 + 拦截接口 ───────────────────
            print("[2/3] 打开目标页并等待内容稳定...", flush=True)
            captured: list[dict] = []
            if args.api_pattern:
                captured = page.capture_responses(args.api_pattern)

            page.goto(args.url, timeout=args.goto_timeout)
            gate.check_or_stop(page)

            text = wait_for_stable_text(
                page,
                args.content_selector,
                min_chars=args.min_chars,
                stable_rounds=args.stable_rounds,
                timeout=args.stable_timeout,
                gate=gate,
                label=args.platform or "content",
            )

            if args.api_pattern:
                page.stop_capture(args.api_pattern)

            # ── 3. 落盘 ────────────────────────────────────
            print("[3/3] 保存结果...", flush=True)
            final_url = page.evaluate("() => window.location.href")
            title = page.title()
            full_html = page.content() or ""
            html_truncated = len(full_html) > args.max_html
            state = _extract_state(page, args.state_key) if args.state_key else None

            record = {
                "url": final_url,
                "title": title,
                "platform": args.platform,
                "collected_at": datetime.now().isoformat(timespec="seconds"),
                "content_selector": args.content_selector,
                "text": text,
                "text_length": len(text),
                "html": full_html[: args.max_html],
                "html_truncated": html_truncated,
                "state": state,
                "api_responses": captured,
                "api_count": len(captured),
            }
            out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

            print(
                f"[OK] 正文 {len(text)} 字符 | 接口 {len(captured)} 个 | HTML "
                f"{'已截断' if html_truncated else '完整'} | 输出 {out}",
                flush=True,
            )
            if not text.strip():
                print(
                    "[WARN] 正文为空：可能是选择器不对、需要登录、或内容在 iframe/shadow DOM 里。"
                    " 用 tools/dom_probe.py 重新确认。",
                    flush=True,
                )
            return 0

    except ChallengeDetected as exc:
        print("\n[STOP] 检测到人机验证/风控墙，已停止（不会重试）。", flush=True)
        print(
            json.dumps(exc.info.to_dict(), ensure_ascii=False, indent=2),
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
