"""示例 02：声明式采集 —— 一个平台只需一份配置。

目标站：quotes.toscrape.com（专门用于练习爬虫的公开站点，允许抓取）。

运行::

    python examples/02_generic_scrape.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.generic import FieldSpec, GenericCollector, PlatformSpec, ScrollSpec


def main() -> int:
    spec = PlatformSpec(
        name="quotes",
        start_url="https://quotes.toscrape.com/",
        card_selector="div.quote",
        fields={
            "text": FieldSpec("span.text"),
            "author": FieldSpec("small.author"),
            "tags": FieldSpec("a.tag", all=True),
            "author_url": FieldSpec("small.author ~ a", attr="href"),
        },
        scroll=ScrollSpec(times=3, delay_min=0.5, delay_max=1.0, stop_if_no_new=1),
        max_items=10,
        headless=True,          # 演示用无头；真实采集建议 False
        data_dir="./profiles/quotes",
    )

    items = GenericCollector(spec).run()
    print(f"\n采集到 {len(items)} 条：")
    for item in items[:3]:
        print(json.dumps(item, ensure_ascii=False, indent=2))

    Path("output").mkdir(exist_ok=True)
    Path("output/quotes.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n已写入 output/quotes.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
