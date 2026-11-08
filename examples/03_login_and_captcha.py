"""示例 03：登录 + 验证码双时钟等待。

适用：需要人工扫码/登录才能采集的平台。
流程：
    1. 打开目标页（有头）
    2. 检测到验证码 → 等用户手动完成（独立预算 100s）
    3. 检测到未登录 → 等用户扫码/登录（独立预算 200s）
    4. 两者都通过后继续执行

运行::

    python examples/03_login_and_captcha.py --platform xiaohongshu
    python examples/03_login_and_captcha.py --platform zhihu --timeout 300

平台登录检测 JS 需要按实际页面调整：
    返回 true 表示「已登录」。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit import LoginStatus, get_platform_captcha_dom_js
from anti_crawl_kit.session import BrowserSession
from anti_crawl_kit.waiting import wait_for_captcha_and_login

# 各平台入口 + 登录检测 JS（按需修改）
PLATFORMS = {
    "xiaohongshu": {
        "url": "https://creator.xiaohongshu.com/",
        # 创作者中心：出现「发布笔记」按钮/头像即视为已登录
        "login_js": """() => {
            var t = document.body ? document.body.innerText : '';
            return t.indexOf('发布笔记') !== -1
                || !!document.querySelector('.user-info, .avatar, [class*="avatar"]');
        }""",
    },
    "zhihu": {
        "url": "https://www.zhihu.com/",
        "login_js": """() => {
            var t = document.body ? document.body.innerText : '';
            return t.indexOf('登录') === -1 && t.indexOf('注册') === -1;
        }""",
    },
    "bilibili": {
        "url": "https://member.bilibili.com/platform/home",
        "login_js": """() => {
            return !!document.querySelector('.user-avatar, .header-avatar, [class*="avatar"]')
                && document.body.innerText.indexOf('登录') === -1;
        }""",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", default="xiaohongshu", choices=sorted(PLATFORMS))
    parser.add_argument("--timeout", type=int, default=200, help="登录预算（秒）")
    args = parser.parse_args()

    cfg = PLATFORMS[args.platform]
    profile = f"./profiles/{args.platform}"

    with BrowserSession(
        headless=False,               # 登录必须有头
        data_dir=profile,             # 同一平台复用登录态
        block_resources=False,        # 登录/验证码阶段必须放行图片（二维码）
    ) as page:
        page.goto(cfg["url"], timeout=60000)

        def check_login(p) -> LoginStatus:
            try:
                ok = bool(p.evaluate(cfg["login_js"], isolated_context=False))
            except Exception as exc:
                return LoginStatus(False, reason=f"check_error:{exc!r}")
            return LoginStatus(ok, method="dom" if ok else "")

        outcome = wait_for_captcha_and_login(
            page=page,
            check_login=check_login,
            captcha_dom_js=get_platform_captcha_dom_js(args.platform),
            login_timeout=args.timeout,
            label=args.platform,
        )

        print("\n等待结果:", outcome)
        if outcome.resolved:
            print("登录成功，可以开始采集。这里放你的采集逻辑。")
            page.screenshot(path=f"output/login_{args.platform}.png")
        else:
            print("未完成登录/验证码，退出。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
