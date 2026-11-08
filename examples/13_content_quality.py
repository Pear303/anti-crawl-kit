"""示例 13：内容质量校验 —— 识别「投毒章节」。

背景：
    有些站点不拦请求，而是往内容源里混「毒章」：
    广告、重复段落、乱码、低熵填充。官方前端会丢弃，裸爬虫会一起扒走。

本示例演示：
    1. 正常章节 → 通过；
    2. 毒章（重复 + 广告）→ 拒绝；
    3. API 标记 polluted=true → 直接失败，不重试；
    4. 批量过滤。

运行::

    python examples/13_content_quality.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.content_quality import check_batch, check_content

NORMAL = (
    "李皓走进巡检司的大门，看到俞姐正在整理文件。他打了个招呼，坐到自己的位置上。"
    "窗外的阳光照进来，落在桌面的卷宗上。今天的工作不多，他打算先把昨天的记录补完。"
) * 5


def main() -> int:
    print("=== 正常章节 ===")
    result = check_content(NORMAL)
    print(f"ok={result.ok} score={result.score} issues={result.reasons}")

    print("\n=== 毒章：重复段落 + 广告 ===")
    poisoned = "\n".join(["请记住本站，最新章节手机阅读。"] * 20)
    result = check_content(poisoned)
    print(f"ok={result.ok} score={result.score}")
    for reason in result.reasons:
        print(f"  - {reason}")

    print("\n=== API 标记 polluted=true ===")
    result = check_content(NORMAL, polluted=True)
    print(f"ok={result.ok} score={result.score} reasons={result.reasons}")
    print("  → 整章丢弃，不重试（重试还是毒章）")

    print("\n=== 批量过滤 ===")
    items = [
        {"title": "第1章", "content": NORMAL},
        {"title": "第2章", "content": "太短"},
        {"title": "第3章", "content": "\n".join(["重复内容填充测试。"] * 15)},
        {"title": "第4章", "content": NORMAL + "\ufffd" * 30},
    ]
    results = check_batch(items)
    for item, quality in zip(items, results):
        first = quality.reasons[0] if quality.reasons else "无问题"
        print(
            f"  {item['title']}: ok={quality.ok} "
            f"score={quality.score} {first}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
