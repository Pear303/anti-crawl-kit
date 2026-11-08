"""人工介入通知 —— 验证墙/登录失效时，把人叫回来。

设计原则：
    - 通知失败绝不能影响主流程（所有渠道 best-effort）。
    - 默认一定有控制台横幅 + 日志；webhook / 桌面通知 / 提示音可选。
    - 不依赖第三方包，Windows/macOS/Linux 都能跑。

环境变量::

    AC_KIT_NOTIFY_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
    AC_KIT_NOTIFY_DESKTOP=1     # 0 关闭桌面通知
    AC_KIT_NOTIFY_SOUND=1       # 0 关闭提示音

Webhook 支持自动识别企业微信 / 飞书 / 钉钉 / Slack，其他地址发通用 JSON。
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotifyResult:
    """单个通知渠道的结果。"""

    channel: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class ChallengeInfo:
    """验证墙/风控现场信息（notify 与 challenge 共用）。"""

    detected: bool
    kind: str = "ok"                 # captcha / blocked / login_required / ok
    reason: str = ""
    url: str = ""
    status: int | None = None
    title: str = ""
    platform: str = ""
    screenshot: str = ""
    html: str = ""
    meta_path: str = ""
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "kind": self.kind,
            "reason": self.reason,
            "url": self.url,
            "status": self.status,
            "title": self.title,
            "platform": self.platform,
            "screenshot": self.screenshot,
            "html": self.html,
            "meta_path": self.meta_path,
            "at": self.at,
        }


def _detect_webhook_format(url: str) -> str:
    lowered = (url or "").lower()
    if "qyapi.weixin.qq.com" in lowered:
        return "wecom"
    if "open.feishu.cn" in lowered or "open.larksuite.com" in lowered:
        return "feishu"
    if "oapi.dingtalk.com" in lowered:
        return "dingtalk"
    if "hooks.slack.com" in lowered:
        return "slack"
    return "json"


def build_webhook_payload(
    fmt: str, title: str, message: str, extra: dict | None = None
) -> dict:
    """构造不同 IM 的 webhook 请求体（纯函数，方便单测）。"""
    text = f"{title}\n{message}"
    if fmt in ("wecom", "dingtalk"):
        return {"msgtype": "text", "text": {"content": text}}
    if fmt == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if fmt == "slack":
        return {"text": f"*{title}*\n{message}"}
    return {"title": title, "text": message, "payload": extra or {}}


class Notifier:
    """多通道通知器。所有 send 都不会抛异常。"""

    def __init__(
        self,
        *,
        webhook: str | None = None,
        desktop: bool | None = None,
        sound: bool | None = None,
        timeout: float = 5.0,
    ):
        self.webhook = (webhook if webhook is not None else os.getenv("AC_KIT_NOTIFY_WEBHOOK", "")).strip()
        self.desktop = (
            desktop if desktop is not None else os.getenv("AC_KIT_NOTIFY_DESKTOP", "1") != "0"
        )
        self.sound = (
            sound if sound is not None else os.getenv("AC_KIT_NOTIFY_SOUND", "1") != "0"
        )
        self.timeout = timeout

    # ── 对外入口 ────────────────────────────────────
    def send(
        self,
        title: str,
        message: str,
        *,
        payload: dict | None = None,
        image_path: str | None = None,
    ) -> list[NotifyResult]:
        """发送通知，返回每个渠道的结果。永不抛异常。"""
        results: list[NotifyResult] = []

        banner = (
            "\n"
            + "=" * 68
            + f"\n[需要人工介入] {title}\n{message}\n"
            + "=" * 68
            + "\n"
        )
        try:
            print(banner, flush=True)
            logger.warning("%s | %s", title, message)
            results.append(NotifyResult("console", True))
        except Exception as exc:  # 极端情况下 stdout 被关闭
            results.append(NotifyResult("console", False, repr(exc)))

        if self.webhook:
            results.append(self._send_webhook(title, message, payload))
        if self.desktop:
            results.append(self._send_desktop(title, message))
        if self.sound:
            results.append(self._send_sound())
        return results

    # ── Webhook ────────────────────────────────────
    def _send_webhook(
        self, title: str, message: str, payload: dict | None
    ) -> NotifyResult:
        fmt = _detect_webhook_format(self.webhook)
        body = build_webhook_payload(fmt, title, message, payload)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.webhook,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "anti-crawl-kit/0.1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(300).decode("utf-8", errors="replace")
                ok = 200 <= resp.status < 300
                return NotifyResult("webhook", ok, f"{fmt} {resp.status} {raw[:160]}")
        except urllib.error.HTTPError as exc:
            return NotifyResult("webhook", False, f"{fmt} HTTP {exc.code}")
        except Exception as exc:
            return NotifyResult("webhook", False, f"{fmt} {exc!r}")

    # ── 桌面通知 ────────────────────────────────────
    def _send_desktop(self, title: str, message: str) -> NotifyResult:
        system = platform.system()
        try:
            if system == "Windows":
                return self._toast_windows(title, message)
            if system == "Darwin":
                script = (
                    f'display notification {json.dumps(message)} '
                    f'with title {json.dumps(title)}'
                )
                subprocess.run(
                    ["osascript", "-e", script],
                    check=True,
                    capture_output=True,
                    timeout=8,
                )
                return NotifyResult("desktop", True, "osascript")
            if system == "Linux" and shutil.which("notify-send"):
                subprocess.run(
                    ["notify-send", title, message],
                    check=True,
                    capture_output=True,
                    timeout=8,
                )
                return NotifyResult("desktop", True, "notify-send")
            return NotifyResult("desktop", False, f"unsupported:{system}")
        except Exception as exc:
            return NotifyResult("desktop", False, repr(exc))

    @staticmethod
    def _toast_windows(title: str, message: str) -> NotifyResult:
        """Windows 10/11 Toast。用 powershell.exe（5.1）兼容性最好。"""
        script = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $template.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($template.CreateTextNode($env:AC_KIT_TOAST_TITLE)) | Out-Null
$nodes.Item(1).AppendChild($template.CreateTextNode($env:AC_KIT_TOAST_BODY)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('anti_crawl_kit').Show($toast)
"""
        env = os.environ.copy()
        env["AC_KIT_TOAST_TITLE"] = title[:120]
        env["AC_KIT_TOAST_BODY"] = message[:500]
        try:
            proc = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                capture_output=True,
                timeout=10,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode == 0:
                return NotifyResult("desktop", True, "windows-toast")
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:160]
            return NotifyResult("desktop", False, f"powershell:{err}")
        except Exception as exc:
            return NotifyResult("desktop", False, repr(exc))

    # ── 提示音 ─────────────────────────────────────
    def _send_sound(self) -> NotifyResult:
        system = platform.system()
        try:
            if system == "Windows":
                import winsound

                winsound.MessageBeep(winsound.MB_ICONHAND)
                return NotifyResult("sound", True, "winsound")
            if system == "Darwin":
                subprocess.run(
                    ["afplay", "/System/Library/Sounds/Glass.aiff"],
                    check=False,
                    timeout=5,
                )
                return NotifyResult("sound", True, "afplay")
            # Linux / 其他：终端响铃
            sys.stdout.write("\a")
            sys.stdout.flush()
            return NotifyResult("sound", True, "bell")
        except Exception as exc:
            return NotifyResult("sound", False, repr(exc))


def notify_challenge(info: ChallengeInfo, notifier: Notifier | None = None) -> list[NotifyResult]:
    """把 ChallengeInfo 格式化成人能看懂的通知。"""
    notifier = notifier or Notifier()
    host = ""
    try:
        host = (info.url or "").split("://", 1)[-1].split("/", 1)[0]
    except Exception:
        pass
    title = f"检测到人机验证/风控墙 · {info.platform or host or '未知站点'}"
    lines = [
        f"类型: {info.kind}",
        f"原因: {info.reason}",
        f"URL : {info.url}",
    ]
    if info.status:
        lines.append(f"HTTP: {info.status}")
    lines.append(f"时间: {info.at}")
    if info.screenshot:
        lines.append(f"截图: {info.screenshot}")
    if info.meta_path:
        lines.append(f"现场: {info.meta_path}")
    lines.append("")
    lines.append("自动化已停止，不会自动重试。请手动完成验证后重新运行。")
    return notifier.send(title, "\n".join(lines), payload=info.to_dict())
