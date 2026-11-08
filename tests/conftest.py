"""测试公共配置。

把 ``src`` 加入 ``sys.path``，这样不安装包也能直接跑测试
（src 布局下 pytest 默认找不到 anti_crawl_kit）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
