"""兼容入口：`python tools/dom_probe.py` 仍然可用。

实现在 ``anti_crawl_kit.tools.dom_probe``；安装后可用 ``ack-dom``。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.tools.dom_probe import main

if __name__ == "__main__":
    raise SystemExit(main())
