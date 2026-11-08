"""一站式页面探测 —— Agent 的「感知层」。

一条命令回答四个问题：

    1. 目标数据在 HTML 里吗？
    2. 有没有接口？在哪？
    3. 是不是被验证墙/登录墙拦了？
    4. 下一步该做什么？

设计原则：
    - 零浏览器：只用 HTTP + JS 包分析。
    - 输出统一 JSON：agent 只读 decision / summary / next_actions。
    - 证据分级：摘要 < 2KB，证据 < 20KB，原文落盘。

用法::

    # 人类可读
    python tools/agent_probe.py --url "https://example.com/page" --field fansNum

    # Agent 消费
    python tools/agent_probe.py --url "..." --field fansNum --json

    # 带登录态
    python tools/agent_probe.py --url "..." --field fansNum \
        --cookie "SESSDATA=xxx" --auth-cookie SESSDATA

    # 只看 HTML，不下载 JS（更快）
    python tools/agent_probe.py --url "..." --field fansNum --no-fetch-js

退出码：
    0  HTML 有数据，或找到了接口候选
    1  需要浏览器 / 未知
    2  被验证墙或登录墙拦住
    3  请求失败
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from anti_crawl_kit.js_bundle import (
    extract_api_hosts,
    extract_script_urls,
    fetch_text,
    hunt_api,
    parse_cookie_header,
)

_LOGIN_KEYWORDS = (
    "请登录",
    "登录后",
    "扫码登录",
    "立即登录",
    "sign in",
    "log in",
    "login",
)
_CAPTCHA_KEYWORDS = (
    "安全验证",
    "验证码",
    "拖动滑块",
    "向右滑动",
    "人机验证",
    "captcha",
    "just a moment",
    "checking your browser",
    "unusual traffic",
)
_BLOCKED_KEYWORDS = (
    "访问异常",
    "请求过于频繁",
    "操作频繁",
    "环境异常",
    "access denied",
    "too many requests",
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SCRIPT_BLOCK_RE = re.compile(
    r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL
)
_STYLE_BLOCK_RE = re.compile(
    r"<style[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    text = _SCRIPT_BLOCK_RE.sub(" ", html or "")
    text = _STYLE_BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _title(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _hit(text: str, words: tuple[str, ...]) -> list[str]:
    lowered = (text or "").lower()
    return [w for w in words if w.lower() in lowered]


def _has_cookie(cookies: dict[str, str], names: list[str]) -> bool:
    return any(name in cookies and cookies.get(name) for name in names)


def _analyze_html(
    html: str,
    status: int,
    url: str,
    *,
    field: str,
    cookies: dict[str, str],
    auth_cookies: list[str],
) -> dict:
    body_text = _strip_tags(html)
    title = _title(html)

    captcha_hits = _hit(body_text, _CAPTCHA_KEYWORDS) or _hit(title, _CAPTCHA_KEYWORDS)
    blocked_hits = _hit(body_text, _BLOCKED_KEYWORDS) or _hit(title, _BLOCKED_KEYWORDS)
    title_login_hits = _hit(title, _LOGIN_KEYWORDS)
    body_login_hits = _hit(body_text, _LOGIN_KEYWORDS)
    login_hits = title_login_hits or body_login_hits
    short_body = len(body_text) < 1500

    field_in_html = bool(field) and field.lower() in (html or "").lower()
    script_urls = extract_script_urls(html, url)
    api_hosts = extract_api_hosts(html)

    try:
        from anti_crawl_kit.page_shape import classify_page_shape

        shape = classify_page_shape(html, status=status)
        page_shape = {
            "kind": shape.kind,
            "advice": shape.advice,
            "states": shape.states,
        }
    except Exception:
        page_shape = {}

    # 页面状态判定（优先级从高到低）
    if status in (429,) or blocked_hits:
        state = "blocked"
        confidence = "high"
        reason = f"http_{status}" if status == 429 else f"keyword:{blocked_hits[0]}"
    elif status in (401,) or (captcha_hits and not field_in_html):
        state = "captcha" if captcha_hits else "login_required"
        confidence = "high"
        reason = (
            f"keyword:{captcha_hits[0]}"
            if captcha_hits
            else "http_401"
        )
    elif status in (403,) and not field_in_html:
        state = "blocked"
        confidence = "medium"
        reason = "http_403"
    elif title_login_hits and not field_in_html:
        state = "login_required"
        confidence = "high"
        reason = f"title:{title_login_hits[0]}"
    elif body_login_hits and short_body and not field_in_html:
        state = "login_required"
        confidence = "medium"
        reason = f"keyword:{body_login_hits[0]}"
    elif auth_cookies and not _has_cookie(cookies, auth_cookies) and not field_in_html:
        state = "login_required"
        confidence = "medium"
        reason = "auth_cookie_missing"
    elif field_in_html:
        state = "html_has_data"
        confidence = "high"
        reason = f"field:{field}"
    else:
        state = "unknown"
        confidence = "low"
        reason = ""

    return {
        "state": state,
        "confidence": confidence,
        "reason": reason,
        "title": title,
        "status": status,
        "field_in_html": field_in_html,
        "script_count": len(script_urls),
        "scripts": script_urls[:10],
        "api_hosts": api_hosts,
        "login_hits": login_hits[:5],
        "captcha_hits": captcha_hits[:5],
        "blocked_hits": blocked_hits[:5],
        "body_chars": len(body_text),
        "has_auth_cookie": _has_cookie(cookies, auth_cookies) if auth_cookies else None,
        "page_shape": page_shape,
    }


def _next_actions(decision: str, evidence: dict, candidates: list[dict]) -> list[str]:
    shape = (evidence.get("page_shape") or {}).get("kind") or ""

    if decision == "blocked":
        return [
            "不要重试：403/429 是风控信号，重试会加深评分",
            "冷却 30~60 分钟后再试",
            "检查代理/IP 出口，确认不是共享 IP",
        ]
    if decision == "captcha":
        return [
            "命中验证墙：用有头浏览器人工完成验证",
            "完成后再跑；不要自动重试",
            "接入 ChallengeGate：截图 + 通知 + 退出",
        ]
    if decision == "login_required":
        return [
            "需要登录：用 examples/03 或 08 打开有头浏览器",
            "确认 auth cookie 名单是否过时（dump 一次真实 cookie）",
            "登录后复用 profile，不要每次重扫",
        ]
    if decision == "html_has_data":
        if shape == "ssr_state":
            return [
                "数据在 SSR 全局状态：优先读 __NEXT_DATA__ / __INITIAL_STATE__ / __NUXT__",
                "HTTP 直接正则提取 JSON，比开浏览器便宜一个量级",
                "状态里没有目标字段时，再用 tools/dom_probe.py 找选择器",
            ]
        return [
            "数据在 HTML 里：跑 tools/dom_probe.py 找选择器",
            "优先读 SSR 状态（__INITIAL_STATE__ / __NEXT_DATA__）",
            "写 YAML 配置，不要写死选择器",
        ]
    if decision == "js_api":
        top = candidates[0] if candidates else {}
        url = top.get("url") or top.get("resolved") or ""
        return [
            f"带 cookie 请求候选接口验证：{url}",
            "返回 200 + JSON → 用 examples/05 直连",
            "返回 403 / 需要签名 → 用 examples/04 浏览器拦截",
        ]
    if decision == "browser_needed":
        return [
            "有 JS 但没找到接口：用 tools/dom_probe.py 看页面实际请求",
            "或用 examples/04 拦截 XHR",
            "页面可能需要登录：先确认登录态",
        ]
    return [
        "手动打开页面确认数据在哪",
        "用 tools/dom_probe.py 看结构和请求",
        "如果 HTML 是空壳，检查是否有外链 JS",
    ]


def _build_script_hint(
    decision: str,
    evidence: dict,
    candidates: list[dict],
    *,
    page_url: str,
    field: str,
) -> dict | None:
    """把发现结果转成「可直接落地的脚本草稿」。

    这是「发现 → 固化」的桥：agent 不需要再从零写代码。
    """
    if decision == "js_api" and candidates:
        top = candidates[0]
        url = top.get("url") or ""
        method = top.get("method") or "GET"
        return {
            "type": "http_json",
            "description": "固定脚本：HTTP 直连候选接口（零浏览器）",
            "target": url,
            "code": (
                "from anti_crawl_kit.http import AntiRiskHttpClient\n\n"
                f'client = AntiRiskHttpClient("demo")\n'
                f'r = client.get_json(\n'
                f'    "{url}",\n'
                f'    headers={{"Referer": "{page_url}"}},\n'
                f")\n"
                "print(r.status_code, r.data)\n"
            ),
            "config": {
                "method": method,
                "url": url,
                "referer": page_url,
                "auth": "需要登录时传 cookies",
            },
        }

    if decision == "html_has_data":
        return {
            "type": "yaml_config",
            "description": "固定脚本：声明式 DOM 采集（先跑 dom_probe 确认选择器）",
            "target": page_url,
            "code": (
                "name: demo\n"
                f"start_url: {page_url}\n"
                'card_selector: "TODO_用 dom_probe 的输出替换"\n'
                "fields:\n"
                f'  {field or "target"}: {{selector: "TODO", attr: text}}\n'
            ),
            "config": {
                "field": field,
                "next": "python tools/dom_probe.py --url 页面URL",
            },
        }

    if decision == "browser_needed":
        return {
            "type": "playwright_intercept",
            "description": "固定脚本：浏览器拦截 XHR（接口藏在运行时）",
            "target": page_url,
            "code": (
                "from anti_crawl_kit import BrowserSession\n\n"
                'with BrowserSession(headless=False, data_dir="./profiles/demo") as page:\n'
                f'    captured = page.capture_responses("/api/")\n'
                f'    page.goto("{page_url}")\n'
                "    page.wait_for_timeout(3000)\n"
                '    page.stop_capture("/api/")\n'
                "    print(len(captured))\n"
            ),
            "config": {"target": page_url},
        }

    return None


def _diff_payload(baseline: dict, current: dict) -> list[dict]:
    """对比基线与当前探测，定位「哪一层变了」。

    这是「回归阶段」的核心：不是重写，而是找出变更点。
    """
    changes: list[dict] = []

    old_decision = baseline.get("decision")
    new_decision = current.get("decision")
    if old_decision != new_decision:
        changes.append(
            {
                "type": "decision_changed",
                "from": old_decision,
                "to": new_decision,
                "hint": "数据源层级变了：先确认是改版还是风控",
            }
        )

    old_candidates = {c.get("url") for c in baseline.get("candidates", []) if c.get("url")}
    new_candidates = {c.get("url") for c in current.get("candidates", []) if c.get("url")}
    if old_candidates and new_candidates and old_candidates != new_candidates:
        changes.append(
            {
                "type": "candidates_changed",
                "added": sorted(new_candidates - old_candidates)[:10],
                "removed": sorted(old_candidates - new_candidates)[:10],
                "hint": "接口路径变了：用 added 里的新地址替换脚本常量",
            }
        )

    old_field = (baseline.get("evidence") or {}).get("field_in_html")
    new_field = (current.get("evidence") or {}).get("field_in_html")
    if old_field != new_field:
        changes.append(
            {
                "type": "field_location_changed",
                "from": old_field,
                "to": new_field,
                "hint": "数据位置变了：从接口改成 DOM，或反过来",
            }
        )

    old_hosts = set((baseline.get("evidence") or {}).get("api_hosts") or [])
    new_hosts = set((current.get("evidence") or {}).get("api_hosts") or [])
    if old_hosts and new_hosts and old_hosts != new_hosts:
        changes.append(
            {
                "type": "api_host_changed",
                "added": sorted(new_hosts - old_hosts),
                "removed": sorted(old_hosts - new_hosts),
                "hint": "接口域名变了：检查脚本里的 baseURL",
            }
        )

    return changes


def _summarize(decision: str, evidence: dict, candidates: list[dict]) -> str:
    shape = (evidence.get("page_shape") or {}).get("kind") or ""
    suffix = f"｜形态={shape}" if shape else ""

    if decision == "blocked":
        return f"被风控拦截（{evidence.get('reason')}），不要重试{suffix}"
    if decision == "captcha":
        return f"命中验证墙（{evidence.get('reason')}），需要人工完成{suffix}"
    if decision == "login_required":
        return f"需要登录（{evidence.get('reason')}）{suffix}"
    if decision == "html_has_data":
        return f"HTML 里包含目标字段（{evidence.get('body_chars')} 字符正文）{suffix}"
    if decision == "js_api":
        hosts = ", ".join(evidence.get("api_hosts") or []) or "页面同域"
        return (
            f"HTML 无目标字段，JS 包中找到 {len(candidates)} 个候选接口"
            f"（{hosts}）{suffix}"
        )
    if decision == "browser_needed":
        return (
            f"HTML 无目标字段，有 {evidence.get('script_count')} 个脚本"
            f"但未提取到接口{suffix}"
        )
    return f"未找到明确数据源，需要人工确认{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 一站式页面探测")
    parser.add_argument("--url", required=True, help="目标页面 URL")
    parser.add_argument("--field", default="", help="想找的字段名，如 fansNum")
    parser.add_argument("--cookie", default="", help='Cookie 字符串，如 "SESSDATA=xxx"')
    parser.add_argument(
        "--auth-cookie",
        action="append",
        default=[],
        help="认证 cookie 名，可重复；用于判断是否登录",
    )
    parser.add_argument("--max-scripts", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument(
        "--no-fetch-js",
        action="store_true",
        help="只分析 HTML，不下载 JS（更快，但找不到接口）",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="只输出 3~5 行摘要（降低 agent 读取成本）",
    )
    parser.add_argument("--out", default="", help="把 JSON 写入文件")
    parser.add_argument(
        "--save-baseline",
        default="",
        help="把当前结果保存为基线（回归对比用）",
    )
    parser.add_argument(
        "--baseline",
        default="",
        help="与基线 JSON 对比，输出变更点（页面改版/接口变动时用）",
    )
    args = parser.parse_args()

    cookies = parse_cookie_header(args.cookie)

    status, html, error = fetch_text(
        args.url,
        referer=args.url,
        cookies=cookies or None,
        timeout=args.timeout,
    )
    if not html:
        payload = {
            "ok": False,
            "tool": "agent_probe",
            "target": args.url,
            "decision": "request_failed",
            "confidence": "high",
            "summary": f"页面请求失败：{error or f'http_{status}'}",
            "evidence": {"status": status, "error": error},
            "candidates": [],
            "next_actions": ["检查 URL / 网络 / 代理", "确认是否需要登录"],
            "artifacts": [],
        }
        return _emit(payload, args)

    evidence = _analyze_html(
        html,
        status,
        args.url,
        field=args.field,
        cookies=cookies,
        auth_cookies=args.auth_cookie,
    )

    candidates: list[dict] = []
    decision = evidence["state"]

    # HTML 没数据、且有脚本 → 查 JS 包
    if (
        decision in ("unknown", "browser_needed")
        and evidence["script_count"] > 0
        and not args.no_fetch_js
    ):
        result = hunt_api(
            args.url,
            html=html,
            cookies=cookies or None,
            fields=[args.field] if args.field else (),
            max_scripts=args.max_scripts,
            timeout=args.timeout,
            loose=False,
        )
        for item in result.endpoints[:8]:
            candidates.append(
                {
                    "method": item.method or "GET",
                    "url": item.resolved,
                    "path": item.value,
                    "source": item.source,
                    "context": item.context[:200],
                    "score": 1.0 if args.field and args.field.lower() in item.context.lower() else 0.6,
                }
            )
        candidates.sort(key=lambda c: (-c["score"], len(c["url"])))
        evidence["api_hosts"] = result.api_hosts or evidence["api_hosts"]
        evidence["field_hits"] = len(result.field_hits)
        evidence["failed_scripts"] = len(result.failed_scripts)
        if candidates:
            decision = "js_api"
        elif evidence["script_count"] > 0:
            decision = "browser_needed"

    summary = _summarize(decision, evidence, candidates)
    next_actions = _next_actions(decision, evidence, candidates)

    payload = {
        "ok": decision in ("html_has_data", "js_api"),
        "tool": "agent_probe",
        "target": args.url,
        "decision": decision,
        "confidence": evidence["confidence"],
        "summary": summary,
        "evidence": evidence,
        "candidates": candidates,
        "script_hint": _build_script_hint(
            decision,
            evidence,
            candidates,
            page_url=args.url,
            field=args.field,
        ),
        "changes": [],
        "next_actions": next_actions,
        "artifacts": [],
    }

    if args.save_baseline:
        baseline_path = Path(args.save_baseline)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payload["artifacts"].append(str(baseline_path))

    if args.baseline:
        try:
            baseline_data = json.loads(
                Path(args.baseline).read_text(encoding="utf-8")
            )
        except Exception as exc:
            payload["changes"] = [{"type": "baseline_error", "error": repr(exc)}]
        else:
            changes = _diff_payload(baseline_data, payload)
            payload["changes"] = changes
            for change in changes:
                payload["next_actions"].append(
                    f"变更 {change['type']}：{change.get('hint', '')}"
                )

    exit_code = 0
    if decision in ("blocked", "captcha", "login_required"):
        exit_code = 2
    elif decision in ("browser_needed", "unknown"):
        exit_code = 1
    elif decision == "request_failed":
        exit_code = 3

    return _emit(payload, args, exit_code=exit_code)


def _emit(payload: dict, args, *, exit_code: int | None = None) -> int:
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload["artifacts"] = [str(out)]
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    text = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.quick and not args.json:
        print(f"decision: {payload['decision']}")
        print(f"summary : {payload['summary']}")
        if payload["candidates"]:
            top = payload["candidates"][0]
            print(f"top     : [{top.get('method', 'GET')}] {top.get('url', '')}")
        if payload.get("changes"):
            print(f"changes : {len(payload['changes'])} 处")
        first = payload["next_actions"][0] if payload["next_actions"] else "-"
        print(f"next    : {first}")
        if exit_code is None:
            exit_code = 0 if payload.get("ok") else 1
        return exit_code

    if args.json:
        print(text)
    else:
        print(f"[{payload['decision']}] {payload['summary']}")
        print(f"置信度: {payload['confidence']}  |  标题: {payload['evidence'].get('title', '')[:60]}")
        if payload["candidates"]:
            print("\n候选接口:")
            for item in payload["candidates"][:5]:
                print(f"  [{item['method']}] {item['url']}")
        if payload.get("changes"):
            print("\n与基线对比:")
            for change in payload["changes"]:
                print(f"  - {change['type']}: {change.get('hint', '')}")
        if payload.get("script_hint"):
            hint = payload["script_hint"]
            print(f"\n脚本草稿（{hint['type']}）:")
            for line in hint["code"].splitlines()[:12]:
                print(f"  {line}")
        print("\n下一步:")
        for action in payload["next_actions"]:
            print(f"  - {action}")
        if payload.get("artifacts"):
            print("\n产物:")
            for artifact in payload["artifacts"]:
                print(f"  {artifact}")

    if exit_code is None:
        exit_code = 0 if payload.get("ok") else 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
