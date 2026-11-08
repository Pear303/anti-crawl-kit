"""控制台编码兜底 + 日志脱敏。

为什么需要它（真实事故）
------------------------
1. 快手标题里含 ``\\xa0``（不间断空格），``print`` 到 Windows GBK 终端抛
   ``UnicodeEncodeError``；外层 ``except`` 把**已经拿到的直链结果**整体吞掉，
   最终回传「缺少可下载 video_url/audio_url」——数据明明已经拿到了。
2. 日志/上报里带 cookie、token、密码，排查时随手一贴就是安全事故。

用法::

    from anti_crawl_kit.console import install_safe_print, redact_secrets

    install_safe_print()          # 进程级兜底，一处覆盖所有模块
    print("标题", title)          # 不会再因为 GBK 编码炸掉主流程

    safe = redact_secrets(raw_log)  # 上报/落盘前脱敏

设计原则：
    - 兜底必须幂等，重复安装不叠加；
    - 兜底只处理编码异常，不改变正常输出；
    - 脱敏宁可多遮，不可漏遮；但只遮值，保留 key 方便排查。
"""
from __future__ import annotations

import builtins
import re
import sys
from typing import Any, Mapping

# 模块导入时先抓住原始 print，避免 install 后自我递归。
_BUILTIN_PRINT = builtins.print
_installed = False


def _stdout_encoding() -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return encoding or "utf-8"


def _encode_safe(value: Any) -> Any:
    """把单个参数转成当前 stdout 能编码的文本；失败时用 replace。"""
    if not isinstance(value, str):
        return value
    encoding = _stdout_encoding()
    try:
        value.encode(encoding)
        return value
    except (UnicodeEncodeError, LookupError):
        try:
            return value.encode(encoding, errors="replace").decode(
                encoding, errors="replace"
            )
        except (UnicodeEncodeError, LookupError):
            # 极端情况：连 replace 都失败，退回 ASCII。
            return value.encode("ascii", errors="replace").decode("ascii")


def safe_print(*args: Any, **kwargs: Any) -> None:
    """print 的编码安全版本：UnicodeEncodeError 时逐参数降级重试。"""
    try:
        _BUILTIN_PRINT(*args, **kwargs)
    except UnicodeEncodeError:
        _BUILTIN_PRINT(*(_encode_safe(arg) for arg in args), **kwargs)


def install_safe_print() -> None:
    """进程级安装 ``safe_print``（幂等）。"""
    global _installed
    if _installed:
        return
    builtins.print = safe_print  # type: ignore[assignment]
    _installed = True


def uninstall_safe_print() -> None:
    """恢复原始 print（测试/嵌入宿主时用）。"""
    global _installed
    if not _installed:
        return
    builtins.print = _BUILTIN_PRINT  # type: ignore[assignment]
    _installed = False


def is_safe_print_installed() -> bool:
    return _installed


# ── 脱敏 ─────────────────────────────────────────────

# 这些 key 的值一律视为敏感（key 本身保留，便于排查）。
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(?:^|[_-])("
    r"cookie|cookies|authorization|auth|token|access_token|refresh_token|"
    r"secret|password|passwd|pwd|session|sessionid|sessdata|ticket|"
    r"api[_-]?key|access[_-]?key|signature|sign"
    r")(?:$|[_-])"
)

# 1) Header 形态：Cookie: xxx / Authorization: Bearer xxx / Set-Cookie: xxx
_HEADER_RE = re.compile(
    r"(?im)\b(set-cookie|cookie|authorization)\s*:\s*[^\r\n]+"
)

# 2) key=value / key: value 形态（值至少 6 个字符，避免误伤短标识）
_KV_RE = re.compile(
    r"(?i)\b("
    r"[A-Za-z0-9_.-]*(?:token|secret|password|passwd|sessdata|sessionid|"
    r"ticket|api[_-]?key|access[_-]?key|signature)[A-Za-z0-9_.-]*"
    r")\s*([=:])\s*(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)

# 3) Bearer / Basic token
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")


def redact_secrets(text: str, *, placeholder: str = "<redacted>") -> str:
    """脱敏文本中的 cookie / token / 密码 / 签名。

    只遮值，保留 key；短值（<6 字符）不处理，避免把 ``token=1`` 这类
    计数误伤成脱敏结果。
    """
    if not text:
        return text
    value = str(text)

    def _header(match: re.Match[str]) -> str:
        return f"{match.group(1)}: {placeholder}"

    def _kv(match: re.Match[str]) -> str:
        raw = match.group(3)
        bare = raw.strip("\"'")
        if len(bare) < 6:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{placeholder}"

    value = _HEADER_RE.sub(_header, value)
    value = _KV_RE.sub(_kv, value)
    value = _BEARER_RE.sub(lambda m: f"{m.group(1)} {placeholder}", value)
    return value


def redact_mapping(
    data: Any,
    *,
    placeholder: str = "<redacted>",
) -> Any:
    """递归脱敏 dict / list 中的敏感字段。

    - dict 的 key 命中敏感词且值是字符串 → 整值替换；
    - 其余字符串走 ``redact_secrets``；
    - 非容器原样返回。
    """
    if isinstance(data, Mapping):
        out: dict[Any, Any] = {}
        for key, value in data.items():
            if (
                isinstance(value, str)
                and value
                and _SENSITIVE_KEY_RE.search(str(key))
                and len(value) >= 6
            ):
                out[key] = placeholder
            else:
                out[key] = redact_mapping(value, placeholder=placeholder)
        return out
    if isinstance(data, list):
        return [redact_mapping(item, placeholder=placeholder) for item in data]
    if isinstance(data, tuple):
        return tuple(redact_mapping(item, placeholder=placeholder) for item in data)
    if isinstance(data, str):
        return redact_secrets(data, placeholder=placeholder)
    return data


__all__ = [
    "install_safe_print",
    "is_safe_print_installed",
    "redact_mapping",
    "redact_secrets",
    "safe_print",
    "uninstall_safe_print",
]
