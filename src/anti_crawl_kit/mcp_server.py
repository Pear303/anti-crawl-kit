"""MCP server —— 把工具包暴露成 agent 可直接调用的能力。

设计原则
--------
1. **薄适配层**：这里只做参数校验、序列化、作业调度；业务逻辑仍然在
   ``anti_crawl_kit`` 的库/CLI 里，不重复实现。
2. **同步 + 异步**：探测/分类/提取几十毫秒到几秒，同步返回；smoke / audit
   可能几十秒到几分钟，走 job 模式（``start`` → ``get_job``）。
3. **可配置但不失控**：``allow_hosts`` 限制目标域；所有输出过 ``redact_mapping``；
   浏览器会话数固定，作业队列有上限。
4. **给 agent 留判断空间**：``run_recipe`` 遇到 ``kind: agent`` 检查点会返回
   ``needs_agent`` + 结构化 handoff；agent 回答后 ``resume_recipe`` 继续。

安装::

    pip install "anti-crawl-kit[mcp]"

启动::

    ack-mcp                                  # stdio（本地 agent）
    ack-mcp --transport http --port 8765     # Streamable HTTP
    ack-mcp --allow-hosts example.com,books.toscrape.com
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .console import redact_mapping
from .browser_pool import BrowserPool
from .recipe import (
    Recipe,
    list_recipes as _list_recipes,
    list_steps,
    load_recipe,
    resume_recipe,
    run_recipe,
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _check_host(url: str, allow_hosts: tuple[str, ...]) -> None:
    """域名白名单；allow_hosts 为空表示不限制（本地开发默认）。"""
    if not allow_hosts:
        return
    host = (urlparse(url).hostname or "").lower()
    for allowed in allow_hosts:
        allowed = allowed.strip().lower()
        if not allowed:
            continue
        if host == allowed or host.endswith("." + allowed):
            return
    raise PermissionError(f"host 不在 allow_hosts 内: {host}（允许: {list(allow_hosts)}）")


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return {"value": value}


# ── 作业队列 ─────────────────────────────────────────

@dataclass
class Job:
    id: str
    tool: str
    status: str = "queued"          # queued / running / done / failed
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    result: Any = None
    error: str = ""
    params: dict[str, Any] = field(default_factory=dict)


class JobManager:
    """内存作业队列。适合单机 MCP server；重启后作业丢失。"""

    def __init__(self, *, max_workers: int = 2, max_jobs: int = 50) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_workers))
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_jobs = max(1, max_jobs)

    def submit(self, tool: str, func: Callable[[], Any], **params: Any) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, tool=tool, params=params)
        with self._lock:
            self._jobs[job_id] = job
            self._prune_locked()
        self._executor.submit(self._run, job_id, func)
        return job_id

    def _prune_locked(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        finished = [
            (job.finished_at, job_id)
            for job_id, job in self._jobs.items()
            if job.status in ("done", "failed")
        ]
        for _, job_id in sorted(finished)[: max(0, len(self._jobs) - self._max_jobs)]:
            self._jobs.pop(job_id, None)

    def _run(self, job_id: str, func: Callable[[], Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "running"
            job.started_at = _now()
        try:
            result = func()
            with self._lock:
                job.result = result
                job.status = "done"
                job.finished_at = _now()
        except Exception as exc:  # 作业失败不拖垮 server
            with self._lock:
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "failed"
                job.finished_at = _now()

    def get(self, job_id: str, *, include_result: bool = False) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"job 不存在: {job_id}")
            data = {
                "job_id": job.id,
                "tool": job.tool,
                "status": job.status,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "error": job.error,
            }
            if include_result:
                data["result"] = job.result
            return data


# ── 工具服务（与 MCP 解耦，可单独测试）────────────────

class ToolService:
    """MCP 工具的实际实现。所有方法返回可 JSON 序列化的 dict。"""

    def __init__(
        self,
        *,
        allow_hosts: tuple[str, ...] = (),
        http_client: Any = None,
        browser_factory: Callable[[], Any] | None = None,
        recipes_dir: str | Path | None = None,
        max_workers: int = 2,
        max_browser_sessions: int = 2,
        browser_idle_ttl: float = 600.0,
    ) -> None:
        self.allow_hosts = tuple(host for host in allow_hosts if host)
        self._http_client = http_client
        self._browser_factory = browser_factory
        self.recipes_dir = Path(recipes_dir) if recipes_dir else Path.cwd() / "recipes"
        self.jobs = JobManager(max_workers=max_workers)
        self._browser_pool: BrowserPool | None = None
        self._max_browser_sessions = max(1, max_browser_sessions)
        self._browser_idle_ttl = browser_idle_ttl

    # ── 基础设施 ──
    def _client(self):
        if self._http_client is None:
            from .http import AntiRiskHttpClient

            self._http_client = AntiRiskHttpClient("mcp", timeout=25, max_retries=2)
        return self._http_client

    def _browser_cm(self):
        if self._browser_factory is not None:
            return self._browser_factory()
        if self._browser_pool is None:
            self._browser_pool = BrowserPool(
                max_sessions=self._max_browser_sessions,
                headless=True,
                idle_ttl=self._browser_idle_ttl,
            )
        return self._browser_pool.lease()

    def close(self) -> None:
        """关闭当前线程的 browser（server 退出时调用）。"""
        if self._browser_pool is not None:
            self._browser_pool.shutdown()

    def _check(self, url: str, allow_hosts: tuple[str, ...] | None = None) -> None:
        _check_host(url, allow_hosts if allow_hosts is not None else self.allow_hosts)

    # ── 同步工具 ──
    def list_capabilities(self) -> dict[str, Any]:
        import importlib.util

        from . import __version__

        def has(name: str) -> bool:
            try:
                return importlib.util.find_spec(name) is not None
            except Exception:
                return False

        return redact_mapping(
            {
                "version": __version__,
                "tools": [
                    "probe_url",
                    "classify_page",
                    "extract_state",
                    "hunt_api",
                    "health_check",
                    "smoke_test",
                    "audit_paths",
                    "prune_profile_cache",
                    "run_recipe",
                    "resume_recipe",
                    "list_recipes",
                    "assess_project",
                    "get_job",
                    "get_job_result",
                    "list_capabilities",
                ],
                "steps": list_steps(),
                "recipes_dir": str(self.recipes_dir),
                "allow_hosts": list(self.allow_hosts),
                "optional_deps": {
                    "curl_cffi": has("curl_cffi"),
                    "patchright": has("patchright"),
                    "yaml": has("yaml"),
                    "mcp": has("mcp"),
                },
                "browser_pool": (
                    self._browser_pool.stats()
                    if self._browser_pool is not None
                    else {"started": False, "max_sessions": self._max_browser_sessions}
                ),
                "job_concurrency": 2,
            }
        )

    def probe_url(
        self,
        url: str,
        *,
        selector: str = "",
        field: str = "",
        deep: bool = False,
        cookie_env: str = "",
    ) -> dict[str, Any]:
        """判断页面形态：HTML 是否有数据 / 接口 / 验证墙。"""
        self._check(url)
        started = time.perf_counter()
        cookies = self._cookies(cookie_env)
        result = self._client().get_text(url, cookies=cookies or None)

        from .empty_shell import detect_empty_shell
        from .page_shape import classify_page_shape, detect_states

        shape = classify_page_shape(result.text, status=result.status_code)
        empty = detect_empty_shell(result.text, selector=selector)
        states = detect_states(result.text)
        decision = self._decision(shape.kind, empty.is_empty)

        payload: dict[str, Any] = {
            "url": url,
            "status": result.status_code,
            "bytes": len(result.text or ""),
            "decision": decision,
            "advice": shape.advice,
            "shape": _as_dict(shape),
            "empty_shell": _as_dict(empty),
            "states": states,
            "next_actions": self._next_actions(decision, shape.kind, empty.is_empty),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
        if deep:
            payload["js"] = self.hunt_api(
                url, fields=[field] if field else [], cookie_env=cookie_env, max_scripts=40
            )
        return redact_mapping(payload)

    def classify_page(self, url: str, *, selector: str = "") -> dict[str, Any]:
        """只做页面形态 + 空壳判定（不下载 JS）。"""
        self._check(url)
        result = self._client().get_text(url)
        from .empty_shell import detect_empty_shell
        from .page_shape import classify_page_shape

        shape = classify_page_shape(result.text, status=result.status_code)
        empty = detect_empty_shell(result.text, selector=selector)
        return redact_mapping(
            {
                "url": url,
                "status": result.status_code,
                "shape": _as_dict(shape),
                "empty_shell": _as_dict(empty),
                "decision": self._decision(shape.kind, empty.is_empty),
            }
        )

    def extract_state(self, url: str, *, preview_chars: int = 2000) -> dict[str, Any]:
        """从 SSR 状态容器提取 JSON（只返回摘要 + 预览，避免超大输出）。"""
        self._check(url)
        import json

        from .state_extract import extract_state

        result = self._client().get_text(url)
        state = extract_state(result.text)
        data = getattr(state, "data", None)
        preview = ""
        if data is not None:
            try:
                preview = json.dumps(data, ensure_ascii=False)[:preview_chars]
            except Exception:
                preview = str(data)[:preview_chars]
        return redact_mapping(
            {
                "url": url,
                "status": result.status_code,
                "ok": bool(getattr(state, "ok", False)),
                "key": getattr(state, "key", ""),
                "error": getattr(state, "error", ""),
                "size": len(data) if isinstance(data, (dict, list)) else 0,
                "preview": preview,
            }
        )

    def hunt_api(
        self,
        url: str,
        *,
        fields: list[str] | None = None,
        cookie_env: str = "",
        max_scripts: int = 60,
        limit: int = 20,
    ) -> dict[str, Any]:
        """从全量 JS 里找接口（慢，5~60s；建议 deep 模式使用）。"""
        self._check(url)
        from .js_bundle import hunt_api

        result = hunt_api(
            url,
            fields=fields or [],
            cookies=self._cookies(cookie_env),
            max_scripts=max(1, min(200, max_scripts)),
        )
        data = result.to_dict()
        return redact_mapping(
            {
                "url": url,
                "discovered_scripts": data["discovered_scripts"],
                "scanned_scripts": data["scanned_scripts"],
                "coverage": data["coverage"],
                "endpoints": data["endpoints"][:limit],
                "base_urls": data["base_urls"][:limit],
                "field_hits": data["field_hits"][:limit],
                "failed_scripts": data["failed_scripts"][:limit],
                "warnings": data["warnings"],
            }
        )

    def health_check(
        self,
        config_path: str,
        *,
        browser: bool = False,
        only: str = "",
    ) -> dict[str, Any]:
        """平台健康体检：静态注册表校验 + 可选免登录探针。"""
        from .health import (
            load_health_config,
            probe_health_page,
            run_static_health_checks,
        )

        module_root, targets = load_health_config(config_path)
        wanted = {item.strip() for item in only.split(",") if item.strip()}
        if wanted:
            targets = [target for target in targets if target.platform in wanted]
        report = run_static_health_checks(targets, module_root=module_root)
        if browser:
            with self._browser_cm() as page:
                for target in targets:
                    report.add(probe_health_page(page, target))
            report.finished_at = _now()
        return redact_mapping(
            {
                "summary": report.counts(),
                "has_fail": report.has_fail(),
                "exit_code": 1 if report.has_fail() else 0,
                "report": report.to_dict(),
            }
        )

    def prune_profile_cache(
        self,
        profile_dir: str,
        *,
        dry_run: bool = False,
        max_cache_mb: float = 200.0,
        max_total_mb: float = 400.0,
        min_interval_hours: float = 24.0,
    ) -> dict[str, Any]:
        """清理浏览器缓存（白名单，绝不碰登录态）。"""
        from .profile_cache import prune_profile_cache

        result = prune_profile_cache(
            profile_dir,
            dry_run=dry_run,
            max_cache_mb=max_cache_mb,
            max_total_mb=max_total_mb,
            min_interval_hours=min_interval_hours,
        )
        return redact_mapping(result.to_dict())

    # ── 异步作业 ──
    def smoke_test(
        self,
        *,
        only: str = "",
        no_browser: bool = False,
        repeat: int = 1,
    ) -> str:
        """启动真实站点冒烟（返回 job_id）。"""
        from .smoke import DEFAULT_CASES

        wanted = {item.strip() for item in only.split(",") if item.strip()}
        cases = [case for case in DEFAULT_CASES if not wanted or case.name in wanted]
        if not cases:
            raise ValueError(f"--only 没有匹配到用例: {sorted(wanted)}")

        def _run() -> dict[str, Any]:
            from .http import AntiRiskHttpClient
            from .smoke import run_smoke
            from .tools.smoke_cli import _merge_attempts

            client = AntiRiskHttpClient("mcp-smoke", timeout=25, max_retries=2)
            attempts = []
            with _maybe_browser(self, not no_browser) as page:
                for _ in range(max(1, repeat)):
                    attempts.append(run_smoke(cases, page=page, http_client=client))
            report = (
                attempts[0]
                if len(attempts) == 1
                else _merge_attempts(attempts)
            )
            data = report.to_dict()
            data["exit_code"] = 1 if report.has_fail() else 0
            return data

        return self.jobs.submit("smoke_test", _run, only=only, no_browser=no_browser, repeat=repeat)

    def audit_paths(
        self,
        config_path: str,
        *,
        only: list[str] | None = None,
        browser_mode: str = "auto",
        out_dir: str = "",
    ) -> str:
        """启动 14 条路径审计（返回 job_id）。"""
        def _run() -> dict[str, Any]:
            from .tools.path_audit import load_audit_config, run_audit

            config = load_audit_config(config_path)
            audit = run_audit(
                config,
                only=only,
                browser_mode=browser_mode,
                out_dir=out_dir or None,
            )
            return redact_mapping(
                {
                    "exit_code": audit.exit_code(),
                    "hard_stop": audit.hard_stop,
                    "report": audit.report().to_dict(),
                }
            )

        return self.jobs.submit("audit_paths", _run, config_path=config_path, only=only)

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self.jobs.get(job_id)

    def get_job_result(self, job_id: str) -> dict[str, Any]:
        return redact_mapping(self.jobs.get(job_id, include_result=True))

    # ── 配方 ──
    def run_recipe(
        self,
        recipe_path: str,
        *,
        inputs: dict[str, Any] | None = None,
        with_browser: bool = False,
        allow_hosts: list[str] | None = None,
    ) -> dict[str, Any]:
        """执行 recipe；需要 agent 判断时返回 ``needs_agent`` + handoff。"""
        recipe = load_recipe(recipe_path)
        hosts = tuple(allow_hosts or self.allow_hosts or recipe.allow_hosts)
        with _maybe_browser(self, with_browser) as page:
            run = run_recipe(
                recipe,
                inputs=inputs or {},
                page=page,
                http_client=self._client(),
                allow_hosts=hosts,
            )
        return redact_mapping(_recipe_payload(run))

    def resume_recipe(
        self,
        run: dict[str, Any],
        value: Any,
        *,
        with_browser: bool = False,
        allow_hosts: list[str] | None = None,
    ) -> dict[str, Any]:
        """用 agent 的回答从检查点继续。``run`` 传上一次的完整返回。"""
        recipe_data = run.get("recipe")
        recipe = Recipe.from_dict(recipe_data) if recipe_data else None
        hosts = tuple(allow_hosts or self.allow_hosts or (recipe.allow_hosts if recipe else ()))
        with _maybe_browser(self, with_browser) as page:
            resumed = resume_recipe(
                run,
                value,
                recipe=recipe,
                page=page,
                http_client=self._client(),
                allow_hosts=hosts,
            )
        return redact_mapping(_recipe_payload(resumed))

    def list_recipes(self, directory: str = "") -> dict[str, Any]:
        root = Path(directory) if directory else self.recipes_dir
        rows = _list_recipes(root)
        return redact_mapping(
            {"directory": str(root), "count": len(rows), "recipes": rows}
        )

    def assess_project(
        self,
        config_path: str,
        *,
        out_dir: str = "",
        no_audit: bool = False,
    ) -> str:
        """启动 G0~G4 技术验证评估（返回 job_id）。"""
        def _run() -> dict[str, Any]:
            from dataclasses import replace

            from .assess import assess, load_assessment_config

            config = load_assessment_config(config_path)
            if no_audit:
                config = replace(config, audit_config="")
            target = out_dir or str(Path("runs") / f"assessment-{config.platform}")
            report = assess(config, out_dir=target)
            report.write(target)
            data = report.to_dict()
            data["exit_code"] = (
                0 if report.decision in ("go", "conditional_go") else 1
            )
            return redact_mapping(data)

        return self.jobs.submit(
            "assess_project", _run, config_path=config_path, no_audit=no_audit
        )

    # ── 辅助 ──
    @staticmethod
    def _cookies(cookie_env: str) -> dict[str, str]:
        if not cookie_env:
            return {}
        raw = os.environ.get(cookie_env, "")
        if not raw:
            return {}
        from .js_bundle import parse_cookie_header

        return parse_cookie_header(raw)

    @staticmethod
    def _decision(shape_kind: str, is_empty: bool) -> str:
        if shape_kind in ("captcha", "blocked"):
            return "stop"
        if shape_kind == "login_wall":
            return "login"
        if shape_kind == "spa_shell" or is_empty:
            return "browser_or_js"
        if shape_kind == "ssr_state":
            return "ssr_state"
        if shape_kind == "ssr":
            return "parse_html"
        return "browser_or_js"

    @staticmethod
    def _next_actions(decision: str, shape_kind: str, is_empty: bool) -> list[str]:
        return {
            "stop": ["停，不要重试；截图 + 通知 + 等待人工处理"],
            "login": ["持久化 profile + 人工登录", "登录后再跑 probe_url"],
            "browser_or_js": [
                "先跑 hunt_api 找接口",
                "没有接口再用浏览器 XHR 拦截 / DOM",
                "HTTP 是空壳时不要直接判 0 条成功",
            ],
            "ssr_state": ["用 extract_state 提 JSON", "比浏览器便宜一个量级"],
            "parse_html": ["直接解析 HTML", "不要开浏览器"],
        }.get(decision, ["先跑 classify_page 再决定路径"])


def _recipe_payload(run) -> dict[str, Any]:
    data = run.to_dict()
    data["exit_code"] = 0 if run.status in ("done", "needs_agent") else 1
    return data


class _maybe_browser:
    """按需打开浏览器；未启用时 yield None。"""

    def __init__(self, service: ToolService, enabled: bool) -> None:
        self._service = service
        self._enabled = enabled
        self._cm = None

    def __enter__(self):
        if not self._enabled:
            return None
        self._cm = self._service._browser_cm()
        return self._cm.__enter__()

    def __exit__(self, exc_type, exc, tb):
        if self._cm is not None:
            return self._cm.__exit__(exc_type, exc, tb)
        return False


# ── MCP 资源 / 提示词（短摘要，避免依赖 docs 目录）────

DECISION_GUIDE = """
路径优先级：JS 包找接口 > HTTP 直连 > SSR 全局状态 > XHR 拦截 > DOM 解析。
先判形态：ssr / ssr_state / spa_shell / login_wall / captcha / blocked。
验证墙是止损信号，不是重试信号：停 + 截图 + 通知 + 退出码 2。
拿到接口不等于能纯 HTTP：必须做签名剥离测试，按返回码判 L1/L3。
未测路径写 not_tested，不能写「不可用」；单次审计不写「生产可用」。
""".strip()

HEALTH_GUIDE = """
任务「成功」但 0 条是静默失效，不是成功。
RunReport 必须带 expected_count / actual_count / degraded_reasons / source_strategy；
actual_count=0 一律 status=empty。
新增平台必须跑 ack-health：模块/类名/entry_param/cookie 名单/选择器逐字校验。
profile 缓存只删白名单；Cookies / Local Storage / IndexedDB / Preferences 绝不碰。
""".strip()

SMOKE_GUIDE = """
发版前必须跑 ack-smoke；mock 通过不算验证。
默认 9 条真实站点用例覆盖 8 类链路：
http_static / http_pagination / js_render / big_list / slow_load /
infinite_scroll / login / negative_control。
负向控制是核心：错误选择器必须判 FAIL；如果它也 PASS，说明检测层坏了。
慢加载不等于失败；诊断与 bench 不截图。
""".strip()

PLATFORM_TEMPLATE = """
platform: demo
label: 示例平台
creator_center_url: ""
supports_login: true
auth_cookies: [sessionid]
cookie_domain: .example.com
qrcode_selector: ".qrcode img"
qrcode_selector_type: role
collector_module: mypkg.collectors.demo
collector_class: DemoCollector
entry_param: uid
needs_login: true
publisher_module: ""
publisher_class: ""
rate_limit: {min_interval_seconds: 3, max_per_hour: 120}
""".strip()


def _prompt_new_platform(url: str, platform: str = "") -> str:
    return f"""接新平台：{platform or url}

1. 先跑 probe_url(url="{url}")，按 decision 选择路径。
2. 需要找接口时再跑 hunt_api（慢，但一次拿全）。
3. 把平台信息填进 PlatformCapability（单一来源）：
   collector_module / collector_class / entry_param / auth_cookies / 探针选择器。
4. 跑 ack-health --config，FAIL 必须为 0。
5. 用 run_recipe 固化「探测 → 提取 → 断言」，需要判断的地方留 kind: agent 检查点。
6. 发版前跑 ack-smoke，负向控制必须 PASS。
"""


def _prompt_regression(url: str, selector: str = "") -> str:
    return f"""改版回归：{url}

1. 先跑 classify_page(url="{url}", selector="{selector}")，确认是改版还是验证墙。
2. 验证墙 → 停，不要重试；改版 → 进下一步。
3. 跑 probe_url 对比 decision 和 shape 是否变化。
4. 选择器失效时：先看兜底是否生效（WARN，不是 OK），再更新主选择器。
5. 跑 ack-smoke 的负向控制，确认「0 条必须判失败」仍然成立。
6. 跑 ack-health，检查注册表/入口/cookie 名单没有漂移。
"""


def _prompt_empty_result(platform: str = "") -> str:
    return f"""排查「任务成功但 0 条」：{platform or "目标平台"}

1. 看 RunReport：actual_count=0 → status 必须是 empty，不能是 ok。
2. 查入口参数：entry_param 是否为空 / 是否漏了平台分支。
3. 查选择器：主选择器 0 命中时，兜底值是否被当成真值。
4. 查登录态：cookie 名单是否过时（Python/JS 两处是否一致）。
5. 查页面形态：HTTP 是否空壳（js_render）；空壳必须走浏览器，不能判成功。
6. 修完跑 ack-health + ack-smoke，负向控制必须 PASS。
"""


# ── MCP server 组装 ──────────────────────────────────

def create_server(service: ToolService | None = None):
    """创建 FastMCP server。需要安装 ``mcp`` extra。"""
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(
            "需要 mcp 包：pip install 'anti-crawl-kit[mcp]'"
        ) from exc

    service = service or ToolService()
    mcp = FastMCP("anti-crawl-kit")

    @mcp.tool()
    def list_capabilities() -> dict:
        """列出可用工具、recipe 步骤、可选依赖和 allow_hosts。"""
        return service.list_capabilities()

    @mcp.tool()
    def probe_url(
        url: str,
        selector: str = "",
        field: str = "",
        deep: bool = False,
        cookie_env: str = "",
    ) -> dict:
        """判断页面：HTML 有数据 / 需要接口 / SPA 空壳 / 登录墙 / 验证码。

        deep=True 会继续扫描 JS 包找接口（慢，5~60s）。
        cookie_env 是环境变量名，不直接传 cookie 值。
        """
        return service.probe_url(
            url, selector=selector, field=field, deep=deep, cookie_env=cookie_env
        )

    @mcp.tool()
    def classify_page(url: str, selector: str = "") -> dict:
        """只做页面形态 + 空壳判定，不下载 JS。"""
        return service.classify_page(url, selector=selector)

    @mcp.tool()
    def extract_state(url: str) -> dict:
        """从 __NEXT_DATA__ / __NUXT__ / __INITIAL_STATE__ 提取 JSON。"""
        return service.extract_state(url)

    @mcp.tool()
    def hunt_api(
        url: str,
        fields: list[str] | None = None,
        cookie_env: str = "",
        max_scripts: int = 60,
    ) -> dict:
        """从全量 JS 包找接口。慢；只在 probe_url 判定需要接口时用。"""
        return service.hunt_api(
            url, fields=fields, cookie_env=cookie_env, max_scripts=max_scripts
        )

    @mcp.tool()
    def health_check(config_path: str, browser: bool = False, only: str = "") -> dict:
        """平台健康体检：注册表/入口/cookie/选择器 + 可选免登录探针。"""
        return service.health_check(config_path, browser=browser, only=only)

    @mcp.tool()
    def smoke_test(only: str = "", no_browser: bool = False, repeat: int = 1) -> dict:
        """启动真实站点冒烟，返回 job_id；用 get_job_result 取结果。"""
        return {
            "job_id": service.smoke_test(
                only=only, no_browser=no_browser, repeat=repeat
            )
        }

    @mcp.tool()
    def audit_paths(
        config_path: str,
        only: list[str] | None = None,
        browser_mode: str = "auto",
        out_dir: str = "",
    ) -> dict:
        """启动 14 条路径审计，返回 job_id。"""
        return {
            "job_id": service.audit_paths(
                config_path, only=only, browser_mode=browser_mode, out_dir=out_dir
            )
        }

    @mcp.tool()
    def prune_profile_cache(
        profile_dir: str,
        dry_run: bool = False,
        max_cache_mb: float = 200.0,
        max_total_mb: float = 400.0,
    ) -> dict:
        """清理浏览器缓存（白名单，不动登录态）。"""
        return service.prune_profile_cache(
            profile_dir,
            dry_run=dry_run,
            max_cache_mb=max_cache_mb,
            max_total_mb=max_total_mb,
        )

    @mcp.tool()
    def get_job(job_id: str) -> dict:
        """查询作业状态（queued / running / done / failed）。"""
        return service.get_job(job_id)

    @mcp.tool()
    def get_job_result(job_id: str) -> dict:
        """取作业完整结果（含 exit_code / report / 失败原因）。"""
        return service.get_job_result(job_id)

    @mcp.tool()
    def run_recipe(
        recipe_path: str,
        inputs: dict | None = None,
        with_browser: bool = False,
        allow_hosts: list[str] | None = None,
    ) -> dict:
        """执行 recipe；遇到 agent 检查点返回 needs_agent + handoff。"""
        return service.run_recipe(
            recipe_path,
            inputs=inputs,
            with_browser=with_browser,
            allow_hosts=allow_hosts,
        )

    @mcp.tool()
    def resume_recipe(
        run: dict,
        value: dict | str | list | int | float | bool | None = None,
        with_browser: bool = False,
        allow_hosts: list[str] | None = None,
    ) -> dict:
        """用 agent 的回答从检查点继续；run 传上一次的完整返回。"""
        return service.resume_recipe(
            run, value, with_browser=with_browser, allow_hosts=allow_hosts
        )

    @mcp.tool()
    def list_recipes(directory: str = "") -> dict:
        """列出可用 recipe（id / title / steps / 是否通过校验）。"""
        return service.list_recipes(directory)

    @mcp.tool()
    def assess_project(
        config_path: str,
        out_dir: str = "",
        no_audit: bool = False,
    ) -> dict:
        """启动 G0~G4 技术验证评估，返回 job_id；用 get_job_result 取 Go/No-Go。"""
        return {
            "job_id": service.assess_project(
                config_path, out_dir=out_dir, no_audit=no_audit
            )
        }

    @mcp.resource("ack://guide/decisions")
    def decisions_guide() -> str:
        """路径优先级、验证墙、签名剥离、未测路径口径。"""
        return DECISION_GUIDE

    @mcp.resource("ack://guide/health")
    def health_guide() -> str:
        """静默失效、RunReport 四字段、健康体检、profile 缓存。"""
        return HEALTH_GUIDE

    @mcp.resource("ack://guide/smoke")
    def smoke_guide() -> str:
        """真实站点冒烟矩阵、负向控制、慢加载语义。"""
        return SMOKE_GUIDE

    @mcp.resource("ack://platform/template")
    def platform_template() -> str:
        """PlatformCapability 模板。"""
        return PLATFORM_TEMPLATE

    @mcp.prompt()
    def new_platform_onboarding(url: str, platform: str = "") -> str:
        """接新平台的标准流程。"""
        return _prompt_new_platform(url, platform)

    @mcp.prompt()
    def regression_after_redesign(url: str, selector: str = "") -> str:
        """改版后的回归流程。"""
        return _prompt_regression(url, selector)

    @mcp.prompt()
    def diagnose_empty_result(platform: str = "") -> str:
        """「任务成功但 0 条」的排查流程。"""
        return _prompt_empty_result(platform)

    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ack-mcp",
        description="anti-crawl-kit MCP server（stdio / streamable-http）",
    )
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--allow-hosts",
        default=os.environ.get("ACK_MCP_ALLOWED_HOSTS", ""),
        help="逗号分隔的域名白名单；空表示不限制（本地开发）",
    )
    parser.add_argument("--recipes-dir", default="", help="recipe 目录（默认 ./recipes）")
    args = parser.parse_args(argv)

    allow_hosts = tuple(
        item.strip() for item in args.allow_hosts.split(",") if item.strip()
    )
    service = ToolService(
        allow_hosts=allow_hosts,
        recipes_dir=args.recipes_dir or None,
    )
    try:
        server = create_server(service)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    if args.transport == "stdio":
        try:
            server.run()
        finally:
            service.close()
    else:
        server.settings.host = args.host
        server.settings.port = args.port
        try:
            server.run(transport="streamable-http")
        finally:
            service.close()
    return 0


__all__ = [
    "DECISION_GUIDE",
    "HEALTH_GUIDE",
    "Job",
    "JobManager",
    "PLATFORM_TEMPLATE",
    "SMOKE_GUIDE",
    "ToolService",
    "create_server",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
