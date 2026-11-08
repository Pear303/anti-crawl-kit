"""从 pyproject.toml 生成 requirements.txt。

单一来源是 pyproject.toml；本脚本只负责导出，避免两处手写漂移。
用法::

    python scripts/gen_requirements.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


def main() -> int:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    extras = project.get("optional-dependencies", {})

    lines = [
        "# 自动生成，勿手改。",
        "# 来源：pyproject.toml",
        "# 重新生成：python scripts/gen_requirements.py",
        "",
        "# 运行时（全部能力）",
    ]
    lines.extend(extras.get("all", []))
    lines.extend(["", "# 开发 / 测试 / 构建"])
    lines.extend(extras.get("dev", []))
    lines.append("")

    out = ROOT / "requirements.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
