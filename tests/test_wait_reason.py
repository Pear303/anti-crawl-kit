"""WaitOutcome 固定失败归因优先级测试。"""

from __future__ import annotations

from anti_crawl_kit.waiting import WaitOutcome, WaitReason, classify_wait_outcome


def _outcome(**overrides) -> WaitOutcome:
    data = dict(
        resolved=True,
        captcha_resolved=True,
        login_ok=True,
        captcha_exhausted=False,
        login_exhausted=False,
        elapsed=1.0,
        last_captcha_reason="",
        last_login_method="cookie",
        reason="ok",
    )
    data.update(overrides)
    return WaitOutcome(**data)


def test_browser_closed_has_highest_priority() -> None:
    outcome = _outcome(captcha_exhausted=True, login_ok=False)
    assert (
        classify_wait_outcome(outcome, browser_closed=True)
        is WaitReason.BROWSER_CLOSED
    )


def test_captcha_not_done_beats_not_logged_in() -> None:
    outcome = _outcome(captcha_exhausted=True, login_ok=False)
    assert classify_wait_outcome(outcome) is WaitReason.CAPTCHA_NOT_DONE


def test_not_logged_in_beats_empty() -> None:
    outcome = _outcome(login_ok=False)
    assert classify_wait_outcome(outcome, item_count=0) is WaitReason.NOT_LOGGED_IN


def test_empty_after_captcha() -> None:
    outcome = _outcome(last_captcha_reason="slider")
    assert (
        classify_wait_outcome(outcome, item_count=0)
        is WaitReason.EMPTY_AFTER_CAPTCHA
    )


def test_general_empty_result() -> None:
    outcome = _outcome()
    assert classify_wait_outcome(outcome, item_count=0) is WaitReason.EMPTY_RESULT


def test_ok_when_items_present() -> None:
    outcome = _outcome()
    assert classify_wait_outcome(outcome, item_count=3) is WaitReason.OK


def test_wait_outcome_default_reason_is_empty() -> None:
    outcome = WaitOutcome(
        resolved=False,
        captcha_resolved=False,
        login_ok=False,
        captcha_exhausted=False,
        login_exhausted=True,
        elapsed=1.0,
    )
    assert outcome.reason == ""
