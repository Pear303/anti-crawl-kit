"""跨 Python 版本的兼容层。

``enum.StrEnum`` 是 Python 3.11 才加入的；项目声明支持 3.10，
这里在旧版本上补一个语义一致的实现。
"""

from __future__ import annotations

try:  # Python 3.11+
    from enum import StrEnum
except ImportError:  # pragma: no cover - 只在 Python 3.10 走到
    from enum import Enum

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        """补齐 3.11 之前缺失的 ``enum.StrEnum``。"""

        def __str__(self) -> str:
            return str(self.value)

        def __format__(self, format_spec: str) -> str:
            return str.__format__(self, format_spec)


__all__ = ["StrEnum"]
