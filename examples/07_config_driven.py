"""示例 07：YAML 配置驱动采集 —— 新平台零代码接入。

运行::

    python examples/07_config_driven.py examples/platforms/quotes.yaml

接入新平台的流程：
    1. python tools/dom_probe.py --url "目标URL"  → 找卡片/字段选择器
    2. 复制一个 yaml，改选择器
    3. 运行本脚本验证
    4. 选择器失效时只改 yaml，不动代码
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.generic import GenericCollector, PlatformSpec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="平台 YAML 配置路径")
    parser.add_argument("--out", default="", help="结果写入 JSON 文件")
    args = parser.parse_args()

    data = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    spec = PlatformSpec.from_dict(data)
    print(f"平台: {spec.name} / 入口: {spec.start_url}")

    items = GenericCollector(spec).run()
    print(f"采集到 {len(items)} 条")
    for item in items[:3]:
        print(json.dumps(item, ensure_ascii=False, indent=2))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("已写入", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
