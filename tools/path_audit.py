"""兼容入口：`python tools/path_audit.py` 仍然可用。

实现在 ``anti_crawl_kit.tools.path_audit``；安装后可用 ``ack-audit``。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.tools.path_audit import main

if __name__ == "__main__":
    raise SystemExit(main())
