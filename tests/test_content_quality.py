"""无浏览器测试：内容质量校验（投毒检测）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.content_quality import check_batch, check_content

NORMAL = (
    "李皓走进巡检司的大门，看到俞姐正在整理文件。他打了个招呼，坐到自己的位置上。"
    "窗外的阳光照进来，落在桌面的卷宗上。今天的工作不多，他打算先把昨天的记录补完。"
) * 5


def test_normal_content_ok():
    result = check_content(NORMAL)
    assert result.ok is True
    assert result.score >= 0.6
    assert result.issues == []


def test_polluted_flag_fails_fast():
    result = check_content(NORMAL, polluted=True)
    assert result.ok is False
    assert result.score == 0.0
    assert result.reasons[0].startswith("polluted")


def test_too_short():
    result = check_content("太短了")
    assert result.ok is False
    assert any(issue.kind == "too_short" for issue in result.issues)


def test_repeated_lines():
    repeated = "\n".join(["这是重复的段落内容，用于测试投毒检测。"] * 12)
    result = check_content(repeated)
    assert result.ok is False
    assert any(issue.kind == "repeated" for issue in result.issues)


def test_ad_keywords_detected():
    text = NORMAL + "\n请记住本站域名，最新章节手机阅读，无弹窗免费阅读。"
    result = check_content(text)
    assert any(issue.kind == "ad_keywords" for issue in result.issues)


def test_mojibake_detected():
    text = NORMAL + "\ufffd" * 20
    result = check_content(text)
    assert any(issue.kind == "mojibake" for issue in result.issues)
    assert result.ok is False


def test_low_entropy_detected():
    result = check_content("啊" * 500)
    assert any(issue.kind == "low_entropy" for issue in result.issues)
    assert result.ok is False


def test_threshold_can_be_relaxed():
    text = NORMAL + "\n请记住本站域名，最新章节手机阅读。"
    strict = check_content(text, threshold=0.9)
    relaxed = check_content(text, threshold=0.5)
    assert strict.ok is False
    assert relaxed.ok is True


def test_check_batch():
    items = [
        {"content": NORMAL},
        {"content": "短"},
        {"content": NORMAL, "polluted": True},
    ]
    results = check_batch(items, text_key="content")
    assert len(results) == 3
    assert results[0].ok is True
    assert results[1].ok is False
    # check_batch 不读 polluted 字段，只校验文本；第三条文本正常
    assert results[2].ok is True
