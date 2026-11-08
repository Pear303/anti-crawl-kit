"""声明式平台采集器 —— 新平台接入的最小成本路径。

思路：一个平台 = 一份 PlatformSpec（入口 URL + 卡片选择器 + 字段选择器 +
滚动策略 + 登录/验证码检测）。不用为每个平台复制一份采集器代码。

适用：列表页结构稳定、字段可从 DOM 直接取（搜索页、榜单、作品列表）。
不适用：需要签名接口 / 复杂交互 / 详情页富文本（用 ApiDiscoverer + 自定义脚本）。

用法::

    from anti_crawl_kit.generic import PlatformSpec, FieldSpec, GenericCollector

    spec = PlatformSpec(
        name="quotes",
        start_url="https://quotes.toscrape.com/",
        card_selector="div.quote",
        fields={
            "text": FieldSpec("span.text"),
            "author": FieldSpec("small.author"),
            "tags": FieldSpec("a.tag", all=True),
        },
    )
    items = GenericCollector(spec).run()
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adaptive_selector import (
    AdaptiveProfile,
    extract_features,
    load_profile,
    relocate,
    save_profile,
)
from .behavior import human_scroll, random_sleep
from .captcha import get_platform_captcha_dom_js
from .login import LoginStatus
from .page_shape import classify_page_shape
from .state_extract import extract_state, pick_path
from .waiting import wait_for_captcha_and_login

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldSpec:
    """单个字段的提取规则。

    attr:
        "text"   → innerText
        "html"   → innerHTML
        其他     → getAttribute(attr)，如 "href" / "src" / "data-id"
    all: True 时返回数组（如标签列表）。
    transform: "count" 时用 parse_count 转成整数。
    state_path: SSR 状态里的字段路径（如 "author.name"）；为空时用字段名。
    """
    selector: str = ""
    attr: str = "text"
    all: bool = False
    transform: str = ""
    state_path: str = ""


@dataclass(frozen=True)
class ScrollSpec:
    times: int = 8
    delay_min: float = 1.0
    delay_max: float = 2.5
    stop_if_no_new: int = 2   # 连续 N 次没有新增就停止


@dataclass
class PlatformSpec:
    name: str
    start_url: str
    card_selector: str
    fields: dict[str, FieldSpec]
    scroll: ScrollSpec = field(default_factory=ScrollSpec)
    # 登录检测 JS：返回 true 表示已登录；为空则视为无需登录
    login_js: str = ""
    # 验证码检测：内置平台名（csdn/zhihu/jianshu/juejin/baijiahao/baidu）
    captcha_platform: str = ""
    captcha_js: str = ""
    # 高级：完全自定义提取 JS，返回 list[dict]，优先于 fields
    extract_js: str = ""
    # SSR 状态优先：状态里列表的路径，如 "home.writerList"
    state_path: str = ""
    # 是否尝试从 SSR 状态取值（默认开；状态取不到会自动回退 DOM）
    state_extract: bool = True
    max_items: int = 0
    headless: bool = False
    data_dir: str = ""
    # 主选择器 0 命中时，用保存的基线自动重定位（默认开启）
    adaptive_fallback: bool = True
    # 基线存储路径；空则用 .adaptive/{name}.json
    adaptive_store: str = ""
    # 重定位最低相似度（0~100）
    adaptive_threshold: float = 40.0

    @classmethod
    def from_dict(cls, data: dict) -> "PlatformSpec":
        """从 YAML/JSON 字典构造配置。

        字段格式::

            fields:
              title: {selector: "h3", attr: "text"}
              url:   {selector: "a", attr: "href"}
              tags:  {selector: "a.tag", all: true}
        """
        fields = {
            name: FieldSpec(**spec) if isinstance(spec, dict) else FieldSpec(selector=str(spec))
            for name, spec in (data.get("fields") or {}).items()
        }
        scroll_data = data.get("scroll") or {}
        scroll = ScrollSpec(**scroll_data) if isinstance(scroll_data, dict) else ScrollSpec()
        return cls(
            name=data["name"],
            start_url=data["start_url"],
            card_selector=data["card_selector"],
            fields=fields,
            scroll=scroll,
            login_js=data.get("login_js", ""),
            captcha_platform=data.get("captcha_platform", ""),
            captcha_js=data.get("captcha_js", ""),
            extract_js=data.get("extract_js", ""),
            state_path=data.get("state_path", ""),
            state_extract=bool(data.get("state_extract", True)),
            max_items=int(data.get("max_items", 0)),
            headless=bool(data.get("headless", False)),
            data_dir=data.get("data_dir", ""),
            adaptive_fallback=bool(data.get("adaptive_fallback", True)),
            adaptive_store=data.get("adaptive_store", ""),
            adaptive_threshold=float(data.get("adaptive_threshold", 40.0)),
        )


def _build_extract_js(spec: PlatformSpec) -> str:
    """把字段规则编译成一段页面内执行的 JS（比逐条 locator 快一个量级）。"""
    import json

    fields_payload = {
        name: {
            "selector": f.selector,
            "attr": f.attr,
            "all": f.all,
        }
        for name, f in spec.fields.items()
    }
    return """(payload) => {
        var cards = Array.from(document.querySelectorAll(payload.card));
        return cards.map(function(card) {
            var row = {};
            Object.keys(payload.fields).forEach(function(name) {
                var f = payload.fields[name];
                var nodes = f.selector ? Array.from(card.querySelectorAll(f.selector)) : [card];
                function read(el) {
                    if (!el) return null;
                    if (f.attr === 'text') return (el.innerText || el.textContent || '').trim();
                    if (f.attr === 'html') return el.innerHTML || '';
                    return el.getAttribute(f.attr) || '';
                }
                if (f.all) {
                    row[name] = nodes.map(read).filter(function(v) { return v !== null && v !== ''; });
                } else {
                    row[name] = read(nodes[0]);
                }
            });
            return row;
        });
    }"""


class GenericCollector:
    """配置驱动的通用列表采集器。"""

    def __init__(self, spec: PlatformSpec, session_kwargs: dict | None = None):
        self.spec = spec
        self.session_kwargs = session_kwargs or {}

    def run(self) -> list[dict]:
        # 惰性导入：没装 patchright 时，本模块仍可用于配置解析与离线测试
        from .session import BrowserSession

        spec = self.spec
        kwargs = {
            "headless": spec.headless,
            "data_dir": spec.data_dir or None,
            "anti_crawl": True,
        }
        kwargs.update(self.session_kwargs)

        with BrowserSession(**kwargs) as page:
            page.goto(spec.start_url, timeout=30000)
            self._wait_ready(page)

            # 状态优先：SSR 状态里有列表时，不滚 DOM、不解析选择器
            raw = self._extract_from_state(page)
            if raw:
                return self._postprocess(raw)

            selector, mode = self._resolve_card_selector(page)
            self._scroll_and_collect(page, selector)
            raw = self._extract(page, selector)
            if mode == "primary" and spec.adaptive_fallback:
                self._maybe_save_profile(page, selector, len(raw))
            elif mode == "adaptive":
                logger.warning(
                    "[%s] 主选择器失效，已用自适应选择器 %r（建议更新 card_selector）",
                    spec.name,
                    selector,
                )
        return self._postprocess(raw)

    # ── 状态优先 ────────────────────────────────────
    def _extract_from_state(self, page) -> list[dict]:
        """从 SSR 状态直接取列表；取不到返回 []，调用方回退 DOM。

        只在 `classify_page_shape` 判定 `ssr_state` 时尝试，
        避免在纯 SSR / SPA 页面上做无意义的整页 JSON 解析。
        """
        spec = self.spec
        if not spec.state_extract or not spec.state_path:
            return []
        try:
            html = page.content()
        except Exception as exc:
            logger.info("[%s] 读取 HTML 失败: %r", spec.name, exc)
            return []

        shape = classify_page_shape(html)
        if shape.kind != "ssr_state":
            return []

        result = extract_state(html)
        if not result.ok:
            logger.info("[%s] 状态提取失败: %s", spec.name, result.error)
            return []

        items = pick_path(result.data, spec.state_path)
        if not isinstance(items, list) or not items:
            logger.info(
                "[%s] 状态路径 %r 不是非空列表，回退 DOM",
                spec.name,
                spec.state_path,
            )
            return []

        rows: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            row: dict[str, Any] = {}
            for name, field in spec.fields.items():
                value = pick_path(item, field.state_path or name)
                if value is None and field.all:
                    value = []
                row[name] = value
            # 整行全空视为无效，避免把空对象写进结果
            if any(value not in (None, "", []) for value in row.values()):
                rows.append(row)

        if not rows:
            logger.info("[%s] 状态字段全为空，回退 DOM", spec.name)
            return []

        logger.info(
            "[%s] 状态命中 %r，%d 条", spec.name, spec.state_path, len(rows)
        )
        return rows

    # ── 自适应兜底 ──────────────────────────────────
    def _profile_path(self) -> Path:
        if self.spec.adaptive_store:
            return Path(self.spec.adaptive_store)
        return Path(".adaptive") / f"{self.spec.name}.json"

    @staticmethod
    def _count(page, selector: str) -> int:
        try:
            return int(
                page.evaluate(
                    "(sel) => document.querySelectorAll(sel).length", selector
                )
                or 0
            )
        except Exception:
            return 0

    def _resolve_card_selector(self, page) -> tuple[str, str]:
        """返回 (实际使用的卡片选择器, 模式)。

        模式：
            primary        主选择器命中
            adaptive       主选择器 0 命中，用基线重定位成功
            no_baseline    主选择器 0 命中，且没有基线
            adaptive_miss  有基线但相似度不够
        """
        spec = self.spec
        primary = spec.card_selector

        count = self._count(page, primary)
        if count == 0:
            # 给懒加载/延迟渲染一次机会，避免把「还没渲染」误判成「改版」
            page.wait_for_selector(primary, timeout=3000)
            count = self._count(page, primary)
        if count > 0:
            return primary, "primary"
        if not spec.adaptive_fallback:
            return primary, "primary"

        profile = load_profile(self._profile_path())
        if profile is None:
            logger.warning(
                "[%s] 主选择器 0 命中，且没有自适应基线（%s）",
                spec.name,
                self._profile_path(),
            )
            return primary, "no_baseline"

        try:
            html = page.content()
        except Exception as exc:
            logger.warning("[%s] 读取页面 HTML 失败: %r", spec.name, exc)
            return primary, "adaptive_miss"

        result = relocate(html, profile.samples[0], threshold=spec.adaptive_threshold)
        if not result.ok:
            logger.warning(
                "[%s] 自适应重定位失败：最高分 %.2f < 阈值 %.2f",
                spec.name,
                result.score,
                spec.adaptive_threshold,
            )
            return primary, "adaptive_miss"

        # 候选选择器要验证命中数：优先选最接近基线数量的
        expected = profile.count or 1
        upper = max(expected * 5, 200)
        best: tuple[str, int] | None = None
        for candidate in result.selectors:
            hit = self._count(page, candidate)
            if hit <= 0 or hit > upper:
                continue
            if best is None or abs(hit - expected) < abs(best[1] - expected):
                best = (candidate, hit)

        if best is None:
            logger.warning(
                "[%s] 重定位到元素但推导不出可用选择器（score=%.2f）",
                spec.name,
                result.score,
            )
            return primary, "adaptive_miss"

        logger.info(
            "[%s] 自适应选择器生效：%s → %s（score=%.2f，命中=%d，基线=%d）",
            spec.name,
            primary,
            best[0],
            result.score,
            best[1],
            expected,
        )
        return best[0], "adaptive"

    def _maybe_save_profile(self, page, selector: str, count: int) -> None:
        """主选择器正常时保存基线，供改版后重定位。"""
        if count <= 0:
            return
        path = self._profile_path()
        existing = load_profile(path)
        if existing is not None and existing.selector == selector:
            # 选择器没变就不重写，避免每次运行都解析整页 HTML
            return
        try:
            html = page.content()
        except Exception:
            return
        samples = extract_features(html, selector, limit=3)
        if not samples:
            # 复杂选择器（含空格/>/:）不在支持范围，静默跳过
            return
        save_profile(
            path,
            AdaptiveProfile(
                name=self.spec.name,
                url=self.spec.start_url,
                selector=selector,
                count=count,
                samples=samples,
            ),
        )
        logger.info("[%s] 已保存自适应基线 %s（%d 条）", self.spec.name, path, count)

    # ── 等待 ────────────────────────────────────────
    def _wait_ready(self, page) -> None:
        spec = self.spec
        captcha_js = spec.captcha_js or get_platform_captcha_dom_js(spec.captcha_platform)

        if spec.login_js:
            def check_login(p) -> LoginStatus:
                try:
                    ok = bool(p.evaluate(spec.login_js, isolated_context=False))
                except Exception as exc:
                    return LoginStatus(False, reason=f"check_error:{exc!r}")
                return LoginStatus(ok, method="js" if ok else "")

            outcome = wait_for_captcha_and_login(
                page=page,
                check_login=check_login,
                captcha_dom_js=captcha_js,
                label=spec.name,
            )
            if not outcome.resolved:
                logger.warning("[%s] 登录/验证码未完成: %s", spec.name, outcome)
        elif captcha_js:
            # 无需登录，但仍要等验证码
            wait_for_captcha_and_login(
                page=page,
                check_login=lambda p: LoginStatus(True, method="no_login_required"),
                captcha_dom_js=captcha_js,
                label=spec.name,
            )

    # ── 滚动 ────────────────────────────────────────
    def _scroll_and_collect(self, page, card_selector: str = "") -> None:
        spec = self.spec
        selector = card_selector or spec.card_selector
        seen = 0
        idle_rounds = 0
        for i in range(spec.scroll.times):
            count = page.evaluate(
                "(sel) => document.querySelectorAll(sel).length", selector
            )
            if count <= seen:
                idle_rounds += 1
                if idle_rounds >= spec.scroll.stop_if_no_new:
                    break
            else:
                idle_rounds = 0
                seen = count
            if spec.max_items and count >= spec.max_items:
                break

            # 优先用人类滚轮；失败回退 JS 滚动
            if random.random() < 0.5:
                human_scroll(page, min_steps=1, max_steps=3)
            else:
                page.evaluate(
                    "window.scrollBy(0, Math.floor(window.innerHeight * 0.9))"
                )
            random_sleep(spec.scroll.delay_min, spec.scroll.delay_max)

    # ── 提取 ────────────────────────────────────────
    def _extract(self, page, card_selector: str = "") -> list[dict]:
        spec = self.spec
        if spec.extract_js:
            result = page.evaluate(spec.extract_js)
            return [r for r in (result or []) if isinstance(r, dict)]
        payload = {
            "card": card_selector or spec.card_selector,
            "fields": {
                name: {"selector": f.selector, "attr": f.attr, "all": f.all}
                for name, f in spec.fields.items()
            },
        }
        result = page.evaluate(_build_extract_js(spec), payload)
        return [r for r in (result or []) if isinstance(r, dict)]

    # ── 后处理 ──────────────────────────────────────
    def _postprocess(self, rows: list[dict]) -> list[dict]:
        from .text import clean_anti_crawl_watermark, parse_count

        out: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            clean: dict[str, Any] = {}
            for name, value in row.items():
                field_spec = self.spec.fields.get(name)
                if isinstance(value, list):
                    clean[name] = [
                        clean_anti_crawl_watermark(v) if isinstance(v, str) else v
                        for v in value
                    ]
                elif isinstance(value, str):
                    v = clean_anti_crawl_watermark(value)
                    if field_spec and field_spec.transform == "count":
                        v = parse_count(v)
                    clean[name] = v
                else:
                    clean[name] = value
            if self.spec.max_items and len(out) >= self.spec.max_items:
                break
            key = str(clean.get("url") or clean.get("id") or clean)
            if key in seen:
                continue
            seen.add(key)
            out.append(clean)
        return out
