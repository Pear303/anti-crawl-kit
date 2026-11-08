"""无浏览器测试：验证墙闸门 / 通知 / 加载更多。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anti_crawl_kit.challenge import ChallengeDetected, ChallengeGate
from anti_crawl_kit.loadmore import LoadMoreDriver
from anti_crawl_kit.notify import ChallengeInfo, Notifier, build_webhook_payload
from anti_crawl_kit.waiting import wait_for_stable_text


class FakePage:
    """只实现 LoadMoreDriver 用到的最小接口。"""

    def __init__(self, counts: list[int]):
        self.counts = list(counts)
        self.scrolls = 0
        self.waits = 0

    def evaluate(self, script, arg=None, **kwargs):
        if "querySelectorAll" in script:
            if len(self.counts) > 1:
                return self.counts.pop(0)
            return self.counts[0] if self.counts else 0
        if "scrollTo" in script or "scrollTop" in script:
            self.scrolls += 1
            return True
        return None

    def wait_for_timeout(self, ms):
        self.waits += 1

    def wait_for_selector(self, selector, timeout=0):
        return True

    def locator(self, selector):
        raise RuntimeError("no locator in fake page")


class FakeGate:
    def __init__(self, fail_at: int | None = None):
        self.calls = 0
        self.fail_at = fail_at

    def check_or_stop(self, page):
        self.calls += 1
        if self.fail_at is not None and self.calls >= self.fail_at:
            raise ChallengeDetected(
                ChallengeInfo(
                    detected=True,
                    kind="captcha",
                    reason="test",
                    url="https://example.com/captcha",
                )
            )


class TextPage:
    def __init__(self, texts: list[str]):
        self.texts = list(texts)

    def evaluate(self, script, arg=None, **kwargs):
        if len(self.texts) > 1:
            return self.texts.pop(0)
        return self.texts[0] if self.texts else ""


# ── 通知 ────────────────────────────────────────────
def test_webhook_payload_formats():
    assert build_webhook_payload("wecom", "标题", "内容") == {
        "msgtype": "text",
        "text": {"content": "标题\n内容"},
    }
    assert build_webhook_payload("dingtalk", "标题", "内容") == {
        "msgtype": "text",
        "text": {"content": "标题\n内容"},
    }
    assert build_webhook_payload("feishu", "标题", "内容") == {
        "msg_type": "text",
        "content": {"text": "标题\n内容"},
    }
    assert build_webhook_payload("slack", "标题", "内容") == {
        "text": "*标题*\n内容"
    }
    assert build_webhook_payload("json", "标题", "内容", {"k": 1}) == {
        "title": "标题",
        "text": "内容",
        "payload": {"k": 1},
    }


def test_notifier_console_only_never_raises():
    n = Notifier(webhook="", desktop=False, sound=False)
    results = n.send("测试标题", "测试内容")
    assert len(results) == 1
    assert results[0].channel == "console"
    assert results[0].ok


def test_challenge_info_roundtrip():
    info = ChallengeInfo(detected=True, kind="captcha", reason="dom", url="https://x")
    data = info.to_dict()
    assert data["detected"] is True
    assert data["kind"] == "captcha"
    assert "at" in data


# ── 闸门 ────────────────────────────────────────────
def test_url_keywords_classify():
    gate = ChallengeGate(notifier=Notifier(webhook="", desktop=False, sound=False))
    assert gate._url_hit("https://x.com/captcha?next=1")[0] == "captcha"
    assert gate._url_hit("https://x.com/risk-control/1")[0] == "blocked"
    assert gate._url_hit("https://x.com/normal/page") is None


# ── 加载更多 ────────────────────────────────────────
def test_load_more_stops_when_no_growth():
    page = FakePage([10, 10, 10, 10])
    driver = LoadMoreDriver(
        page,
        item_selector=".item",
        max_rounds=10,
        idle_rounds=2,
        delay=(0, 0),
        wait_after=0,
        initial_timeout=0,
    )
    result = driver.run()
    assert result.stopped_reason == "no_growth"
    assert result.item_count == 10
    assert result.rounds == 2
    assert page.scrolls == 2


def test_load_more_stops_at_max_rounds():
    page = FakePage([10, 20, 30])
    driver = LoadMoreDriver(
        page,
        item_selector=".item",
        max_rounds=2,
        idle_rounds=3,
        delay=(0, 0),
        wait_after=0,
        initial_timeout=0,
    )
    result = driver.run()
    assert result.stopped_reason == "max_rounds"
    assert result.item_count == 30
    assert result.rounds == 2


def test_load_more_raises_on_challenge():
    page = FakePage([10, 10, 10])
    gate = FakeGate(fail_at=1)
    driver = LoadMoreDriver(
        page,
        item_selector=".item",
        gate=gate,
        max_rounds=5,
        idle_rounds=3,
        delay=(0, 0),
        wait_after=0,
        initial_timeout=0,
    )
    try:
        driver.run()
    except ChallengeDetected as exc:
        assert exc.info.kind == "captcha"
    else:
        raise AssertionError("应当抛出 ChallengeDetected")


def test_load_more_can_return_challenge_result():
    page = FakePage([10, 10, 10])
    gate = FakeGate(fail_at=1)
    driver = LoadMoreDriver(
        page,
        item_selector=".item",
        gate=gate,
        max_rounds=5,
        idle_rounds=3,
        delay=(0, 0),
        wait_after=0,
        initial_timeout=0,
        raise_on_challenge=False,
    )
    result = driver.run()
    assert result.stopped_reason == "challenge"
    assert result.challenge is not None
    assert result.challenge.kind == "captcha"


# ── 渲染稳定 ────────────────────────────────────────
def test_wait_for_stable_text_returns_when_unchanged():
    page = TextPage(["hello world " * 10, "hello world " * 10, "hello world " * 10])
    text = wait_for_stable_text(
        page,
        "article",
        min_chars=20,
        stable_rounds=2,
        timeout=2,
        poll=0.01,
    )
    assert "hello world" in text
