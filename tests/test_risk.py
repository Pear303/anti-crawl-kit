"""HTTP 风险分类的离线测试。"""

from __future__ import annotations

from anti_crawl_kit.risk import (
    RiskDecision,
    RiskKind,
    classify_http_result,
    page_title,
    visible_text,
)


def test_visible_text_strips_script_style_and_tags() -> None:
    html = """
    <html><head><title>T</title><style>.a{color:red}</style>
    <script>var captcha = 1;</script></head>
    <body><p>你好   世界</p></body></html>
    """
    text = visible_text(html)
    assert "captcha" not in text
    assert "color:red" not in text
    assert "<p>" not in text
    assert "你好 世界" in text


def test_visible_text_respects_limit() -> None:
    assert visible_text("x" * 5000, limit=10) == "x" * 10


def test_page_title_handles_missing_and_multiline() -> None:
    assert page_title("<html></html>") == ""
    assert page_title("<title>\n  Just a moment  \n</title>") == "Just a moment"


def test_429_is_rate_limited() -> None:
    decision = classify_http_result(429, "")
    assert decision.kind is RiskKind.RATE_LIMITED
    assert decision.should_fallback_to_browser is True


def test_401_403_blocked() -> None:
    for code in (401, 403):
        decision = classify_http_result(code, "")
        assert decision.kind is RiskKind.BLOCKED
        assert decision.reason == f"http_{code}"


def test_5xx_is_network_error() -> None:
    decision = classify_http_result(502, "bad gateway")
    assert decision.kind is RiskKind.NETWORK_ERROR
    assert decision.should_fallback_to_browser is True


def test_202_challenge_detected_but_json_body_exempt() -> None:
    assert classify_http_result(202, "please wait").kind is RiskKind.BLOCKED
    assert classify_http_result(202, '{"data": []}').kind is RiskKind.OK


def test_short_json_login_wall() -> None:
    decision = classify_http_result(200, '{"ok":-100,"url":"/login.php"}')
    assert decision.kind is RiskKind.LOGIN_REQUIRED
    assert decision.reason == "login_json"


def test_captcha_url_beats_visible_text() -> None:
    decision = classify_http_result(200, "<html>正常</html>", "https://x.test/captcha?x=1")
    assert decision.kind is RiskKind.CAPTCHA
    assert decision.reason == "captcha_url"


def test_captcha_visible_text_only_counts_page_body() -> None:
    body = "<html><body><div>请完成验证</div></body></html>"
    assert classify_http_result(200, body).reason == "captcha_visible"

    # JS bundle 里出现验证码字样，不应误判
    js_only = '<html><body><script>var t="请完成验证"</script>普通内容</body></html>'
    assert classify_http_result(200, js_only).kind is RiskKind.OK


def test_captcha_title() -> None:
    decision = classify_http_result(200, "<html><title>Just a moment...</title></html>")
    assert decision.kind is RiskKind.CAPTCHA
    assert decision.reason == "captcha_title"


def test_login_visible_only_for_short_pages() -> None:
    short = "<html><body>请先登录</body></html>"
    assert classify_http_result(200, short).reason == "login_visible"

    long_page = "<html><body>" + ("内容" * 400) + "请先登录</body></html>"
    assert classify_http_result(200, long_page).kind is RiskKind.OK


def test_login_url() -> None:
    decision = classify_http_result(200, "<html>abc</html>", "https://x.test/signin?next=/")
    assert decision.kind is RiskKind.LOGIN_REQUIRED
    assert decision.reason == "login_url"


def test_signature_failure_only_for_short_responses() -> None:
    assert classify_http_result(200, "invalid signature").kind is RiskKind.SIGNATURE_FAILED
    big = "w_rid " + ("x" * 3000)
    assert classify_http_result(200, big).kind is RiskKind.OK


def test_ok_response() -> None:
    decision = classify_http_result(200, "<html><body>正常页面内容</body></html>")
    assert decision == RiskDecision(RiskKind.OK, "ok", False)
