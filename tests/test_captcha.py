"""验证码检测的离线测试：URL 规则、平台 JS 注册表、DOM 兜底、等待循环。"""

from __future__ import annotations

from typing import Any

from anti_crawl_kit.captcha import (
    CaptchaDetection,
    detect_captcha_page,
    detect_captcha_url,
    get_platform_captcha_dom_js,
    wait_until_captcha_resolved,
)


def test_url_keywords_detected() -> None:
    for url in (
        "https://x.test/captcha?x=1",
        "https://x.test/verify",
        "https://x.test/challenge",
        "https://x.test/slider.html",
    ):
        result = detect_captcha_url(url)
        assert result.detected is True
        assert result.reason.startswith("url_")


def test_phone_bind_url() -> None:
    result = detect_captcha_url("https://x.test/bind-phone")
    assert result.detected is True
    assert result.reason == "phone_bind_url"

    assert detect_captcha_url("https://x.test/bind-email").detected is False


def test_plain_url_not_detected() -> None:
    result = detect_captcha_url("https://x.test/list")
    assert result == CaptchaDetection(False, "", "https://x.test/list")
    assert detect_captcha_url("").detected is False


def test_platform_dom_js_registry() -> None:
    assert "cf_title" in (get_platform_captcha_dom_js("csdn") or "")
    assert "SignFlow-verifyCode" in (get_platform_captcha_dom_js("ZHIHU") or "")
    assert "geetest_widget" in (get_platform_captcha_dom_js("juejin") or "")
    assert "百度安全验证" in (get_platform_captcha_dom_js("baidu") or "")
    assert get_platform_captcha_dom_js("no-such-platform") is None
    assert get_platform_captcha_dom_js("") is None


class UrlPage:
    """evaluate 第一段返回 URL，第二段按注入的 DOM 结果返回。"""

    def __init__(self, url: str, dom_result: Any = None, url_error: str = "") -> None:
        self.url = url
        self.dom_result = dom_result
        self.url_error = url_error
        self.calls = 0

    def evaluate(self, script: str) -> Any:
        self.calls += 1
        if self.calls == 1:
            if self.url_error:
                raise RuntimeError(self.url_error)
            return self.url
        if self.dom_result is None:
            return {"detected": False}
        if isinstance(self.dom_result, Exception):
            raise self.dom_result
        return self.dom_result


def test_detect_page_by_url_skips_dom() -> None:
    page = UrlPage("https://x.test/captcha")
    result = detect_captcha_page(page, dom_detect_js="() => ({detected: true})")
    assert result.detected is True
    assert page.calls == 1


def test_detect_page_falls_back_to_dom() -> None:
    page = UrlPage("https://x.test/list", {"detected": True, "reason": "geetest_dom"})
    result = detect_captcha_page(page, dom_detect_js="() => 1")
    assert result == CaptchaDetection(True, "geetest_dom", "")


def test_detect_page_dom_truthy_non_dict() -> None:
    page = UrlPage("https://x.test/list", True)
    result = detect_captcha_page(page, dom_detect_js="() => 1")
    assert result.detected is True
    assert result.reason == "dom_detected"


def test_detect_page_dom_error_is_not_detected() -> None:
    page = UrlPage("https://x.test/list", RuntimeError("evaluate timeout"))
    result = detect_captcha_page(page, dom_detect_js="() => 1")
    assert result.detected is False
    assert result.reason == "dom_detect_error"


def test_detect_page_url_error_uses_dom() -> None:
    page = UrlPage("", {"detected": True, "reason": "cf_body"}, url_error="navigation")
    result = detect_captcha_page(page, dom_detect_js="() => 1")
    assert result.reason == "cf_body"


def test_detect_page_url_error_without_dom() -> None:
    page = UrlPage("", url_error="navigation")
    assert detect_captcha_page(page).detected is False


def test_wait_until_resolved_when_already_gone() -> None:
    page = UrlPage("https://x.test/list", {"detected": False})
    assert wait_until_captcha_resolved(page, timeout_seconds=1, poll_seconds=0) is True


def test_wait_until_timeout_when_still_detected() -> None:
    page = UrlPage("https://x.test/captcha")
    assert wait_until_captcha_resolved(page, timeout_seconds=0, poll_seconds=0) is False
