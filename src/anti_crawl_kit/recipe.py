"""可配置的采集配方（recipe）—— 确定性骨架 + agent 检查点。

为什么需要它
------------
纯脚本：稳定、快，但站点改版或遇到没见过的结构就卡死。
纯 agent：灵活，但每次都要现场试错，慢且不可复现。

recipe 把两者拆开：

    - 确定性步骤（http_get / browser_count / assert / ...）由代码执行；
    - 需要判断的地方留 ``kind: agent`` 检查点，把结构化上下文交给 agent；
    - agent 返回结果后 ``resume_recipe`` 从断点继续；
    - 失败也可以转成检查点（``on_error: agent``），而不是直接中断。

这样「常见路径」极快且可回归，「意外情况」仍然有 agent 的灵活性。

配方示例::

    id: product_list
    title: 商品列表探测
    inputs:
      url: {type: string, required: true}
    steps:
      - id: fetch
        uses: http_get
        with: {url: "${inputs.url}"}
      - id: classify
        uses: classify_html
        with: {html: "${steps.fetch.text}"}
      - id: pick_selector
        kind: agent
        ask:
          prompt: "根据页面形态选出最稳定的卡片选择器"
          schema:
            type: object
            properties: {selector: {type: string}}
            required: [selector]
          context_keys: [steps.classify.shape]
      - id: count
        uses: count_html
        with:
          html: "${steps.fetch.text}"
          selector: "${steps.pick_selector.selector}"
      - id: check
        uses: assert
        with: {value: "${steps.count.count}", op: gte, expected: 10}

用法::

    from anti_crawl_kit.recipe import load_recipe, run_recipe, resume_recipe

    run = run_recipe(load_recipe("recipes/product_list.yaml"), inputs={"url": "..."})
    if run.status == "needs_agent":
        answer = {"selector": "article.product_pod"}
        run = resume_recipe(run, answer)
"""

from __future__ import annotations

import ast
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse


# ── 数据模型 ─────────────────────────────────────────

@dataclass(frozen=True)
class AgentAsk:
    """agent 检查点的提问定义。"""

    prompt: str
    schema: dict[str, Any] = field(default_factory=dict)
    context_keys: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Step:
    """一个配方步骤。"""

    id: str
    uses: str = ""
    with_: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    when: str = ""
    on_error: str = "stop"          # stop / continue / agent
    kind: str = "step"              # step / agent
    ask: AgentAsk | None = None
    save_as: str = ""

    @property
    def output_key(self) -> str:
        return self.save_as or self.id


@dataclass
class StepRun:
    id: str
    uses: str
    status: str                     # ok / failed / skipped / needs_agent
    output: Any = None
    error: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uses": self.uses,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepRun":
        return cls(
            id=str(data.get("id", "")),
            uses=str(data.get("uses", "")),
            status=str(data.get("status", "")),
            output=data.get("output"),
            error=str(data.get("error", "")),
            elapsed_ms=int(data.get("elapsed_ms", 0) or 0),
        )


@dataclass
class RecipeRun:
    """一次配方执行结果。可序列化，支持跨进程 resume。"""

    run_id: str
    recipe_id: str
    status: str                     # done / needs_agent / failed
    context: dict[str, Any] = field(default_factory=dict)
    steps: list[StepRun] = field(default_factory=list)
    handoff: dict[str, Any] | None = None
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    # 配方快照：resume 时不需要客户端再传原文件（跨进程可用）
    recipe_snapshot: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "recipe_id": self.recipe_id,
            "status": self.status,
            "context": self.context,
            "steps": [step.to_dict() for step in self.steps],
            "handoff": self.handoff,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "recipe": self.recipe_snapshot,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecipeRun":
        return cls(
            run_id=str(data.get("run_id", "")),
            recipe_id=str(data.get("recipe_id", "")),
            status=str(data.get("status", "")),
            context=dict(data.get("context") or {}),
            steps=[StepRun.from_dict(item) for item in (data.get("steps") or [])],
            handoff=dict(data.get("handoff") or {}) or None,
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
            error=str(data.get("error", "")),
            recipe_snapshot=(
                dict(data["recipe"]) if isinstance(data.get("recipe"), Mapping) else None
            ),
        )


@dataclass(frozen=True)
class Recipe:
    id: str
    title: str = ""
    description: str = ""
    version: str = "1"
    inputs: dict[str, Any] = field(default_factory=dict)
    steps: tuple[Step, ...] = ()
    outputs: dict[str, Any] = field(default_factory=dict)
    allow_hosts: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "version": self.version,
            "inputs": self.inputs,
            "steps": [
                {
                    "id": step.id,
                    "uses": step.uses,
                    "with": step.with_,
                    "description": step.description,
                    "when": step.when,
                    "on_error": step.on_error,
                    "kind": step.kind,
                    "ask": (
                        {
                            "prompt": step.ask.prompt,
                            "schema": step.ask.schema,
                            "context_keys": list(step.ask.context_keys),
                            "suggestions": list(step.ask.suggestions),
                        }
                        if step.ask
                        else None
                    ),
                    "save_as": step.save_as,
                }
                for step in self.steps
            ],
            "outputs": self.outputs,
            "allow_hosts": list(self.allow_hosts),
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Recipe":
        steps: list[Step] = []
        for raw in data.get("steps") or []:
            if not isinstance(raw, Mapping):
                raise ValueError("steps 每一项必须是映射")
            kind = str(raw.get("kind", "step"))
            ask_raw = raw.get("ask") or {}
            ask = None
            if kind == "agent":
                ask = AgentAsk(
                    prompt=str(ask_raw.get("prompt", "")),
                    schema=dict(ask_raw.get("schema") or {}),
                    context_keys=tuple(str(x) for x in (ask_raw.get("context_keys") or ())),
                    suggestions=tuple(str(x) for x in (ask_raw.get("suggestions") or ())),
                )
            steps.append(
                Step(
                    id=str(raw.get("id", "")),
                    uses=str(raw.get("uses", "")),
                    with_=dict(raw.get("with") or raw.get("params") or {}),
                    description=str(raw.get("description", "")),
                    when=str(raw.get("when", "")),
                    on_error=str(raw.get("on_error", "stop")),
                    kind=kind,
                    ask=ask,
                    save_as=str(raw.get("save_as", "")),
                )
            )
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            version=str(data.get("version", "1")),
            inputs=dict(data.get("inputs") or {}),
            steps=tuple(steps),
            outputs=dict(data.get("outputs") or {}),
            allow_hosts=tuple(str(x) for x in (data.get("allow_hosts") or ())),
            meta=dict(data.get("meta") or {}),
        )


# ── 运行时 ───────────────────────────────────────────

@dataclass
class RecipeRuntime:
    """步骤执行时可用的资源。"""

    inputs: dict[str, Any]
    context: dict[str, Any]
    page: Any = None
    http_client: Any = None
    allow_hosts: tuple[str, ...] = ()


StepFunc = Callable[[dict[str, Any], RecipeRuntime], Any]

_STEPS: dict[str, StepFunc] = {}
_STEP_DESCRIPTIONS: dict[str, str] = {}


def register_step(
    name: str,
    func: StepFunc | None = None,
    *,
    description: str = "",
    replace: bool = False,
):
    """注册自定义步骤（扩展点）。

    用法::

        @register_step("my_step", description="我的步骤")
        def my_step(params, runtime):
            return {"ok": True}
    """
    def decorator(fn: StepFunc) -> StepFunc:
        if name in _STEPS and not replace:
            raise ValueError(f"步骤已存在: {name}（需要覆盖请传 replace=True）")
        _STEPS[name] = fn
        _STEP_DESCRIPTIONS[name] = description or (fn.__doc__ or "").strip()
        return fn

    if func is not None:
        return decorator(func)
    return decorator


def list_steps() -> dict[str, str]:
    return dict(_STEP_DESCRIPTIONS)


def _check_url(url: str, allow_hosts: Iterable[str]) -> None:
    hosts = [host.strip().lower() for host in allow_hosts if host and host.strip()]
    if not hosts:
        return
    host = (urlparse(url).hostname or "").lower()
    for allowed in hosts:
        if host == allowed or host.endswith("." + allowed):
            return
    raise PermissionError(f"host 不在 allow_hosts 内: {host}（允许: {hosts}）")


def _lookup(path: str, context: Mapping[str, Any]) -> Any:
    current: Any = context
    for part in path.strip().split("."):
        if not part:
            continue
        if isinstance(current, Mapping):
            if part not in current:
                raise KeyError(f"模板变量不存在: ${{{path}}}（缺少 {part}）")
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.lstrip("-").isdigit():
            current = current[int(part)]
        else:
            raise KeyError(f"模板变量无法取值: ${{{path}}}（在 {part} 处）")
    return current


_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")


def resolve_value(value: Any, context: Mapping[str, Any]) -> Any:
    """递归解析 ``${inputs.url}`` / ``${steps.fetch.status}``。"""
    if isinstance(value, str):
        whole = _TEMPLATE_RE.fullmatch(value.strip())
        if whole:
            return _lookup(whole.group(1), context)
        return _TEMPLATE_RE.sub(
            lambda match: str(_lookup(match.group(1), context)), value
        )
    if isinstance(value, dict):
        return {key: resolve_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_value(item, context) for item in value]
    if isinstance(value, tuple):
        return tuple(resolve_value(item, context) for item in value)
    return value


_ALLOWED_AST = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.In,
    ast.NotIn,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Attribute,
)


class _Box(dict):
    """让 ``when`` 表达式支持 ``steps.fetch.status`` 这种点号取值。"""

    def __getattr__(self, name: str) -> Any:
        if name in self:
            return self[name]
        raise AttributeError(name)


def _box(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _Box({key: _box(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_box(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_box(item) for item in value)
    return value


def eval_when(expression: str, context: Mapping[str, Any]) -> bool:
    """安全执行 ``when`` 表达式（只允许比较/布尔/常量/容器/点号取值）。"""
    expression = (expression or "").strip()
    if not expression:
        return True
    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST):
            raise ValueError(f"when 表达式包含不允许的语法: {type(node).__name__}")
        # 禁止 __class__ / __globals__ 这类反射属性
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"when 表达式不允许访问私有属性: {node.attr}")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ValueError(f"when 表达式不允许访问私有名称: {node.id}")
    namespace = {"__builtins__": {}, **_box(context)}
    return bool(
        eval(  # noqa: S307 - AST 已白名单，且无 builtins
            compile(tree, "<when>", "eval"),
            namespace,
        )
    )


# ── 内置步骤 ─────────────────────────────────────────

@register_step("http_get", description="HTTP GET，返回 status/text/url")
def _step_http_get(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    _check_url(str(params["url"]), runtime.allow_hosts)
    if runtime.http_client is None:
        from .http import AntiRiskHttpClient

        runtime.http_client = AntiRiskHttpClient(
            "recipe", timeout=int(params.get("timeout", 20))
        )
    result = runtime.http_client.get_text(
        str(params["url"]),
        headers=params.get("headers"),
        cookies=params.get("cookies"),
    )
    return {
        "status": result.status_code,
        "url": result.url,
        "text": result.text,
        "bytes": len(result.text or ""),
    }


@register_step("http_json", description="HTTP GET JSON，可选 dot-path 取值")
def _step_http_json(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    _check_url(str(params["url"]), runtime.allow_hosts)
    if runtime.http_client is None:
        from .http import AntiRiskHttpClient

        runtime.http_client = AntiRiskHttpClient(
            "recipe", timeout=int(params.get("timeout", 20))
        )
    result = runtime.http_client.get_json(
        str(params["url"]),
        headers=params.get("headers"),
        cookies=params.get("cookies"),
    )
    output = {"status": result.status_code, "url": result.url, "data": result.data}
    path = str(params.get("path", "")).strip()
    if path:
        current = result.data
        for part in path.split("."):
            if isinstance(current, Mapping):
                current = current.get(part)
            elif isinstance(current, list) and part.lstrip("-").isdigit():
                current = current[int(part)]
            else:
                current = None
                break
        output["value"] = current
    return output


@register_step("http_request", description="HTTP 请求（GET/POST，支持 cookies_env）")
def _step_http_request(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    """通用 HTTP 步骤：解决 POST 接口和鉴权 cookie 的配置化问题。

    ``cookies_env`` 指向环境变量名，不把 cookie 写进 YAML。
    """
    method = str(params.get("method", "GET")).upper()
    url = str(params["url"])
    _check_url(url, runtime.allow_hosts)
    if runtime.http_client is None:
        from .http import AntiRiskHttpClient

        runtime.http_client = AntiRiskHttpClient(
            "recipe", timeout=int(params.get("timeout", 20))
        )

    cookies = params.get("cookies")
    cookie_env = str(params.get("cookies_env", "")).strip()
    if cookie_env:
        raw = os.environ.get(cookie_env, "")
        if raw:
            from .js_bundle import parse_cookie_header

            cookies = parse_cookie_header(raw)

    headers = params.get("headers")
    if method == "POST":
        result = runtime.http_client.post_json(
            url,
            json_data=params.get("json") or {},
            headers=headers,
            cookies=cookies,
        )
        return {
            "status": result.status_code,
            "url": result.url,
            "data": result.data,
            "text": result.text,
        }
    if method == "GET":
        if params.get("parse_json", True):
            result = runtime.http_client.get_json(
                url, headers=headers, cookies=cookies
            )
        else:
            result = runtime.http_client.get_text(
                url, headers=headers, cookies=cookies
            )
        return {
            "status": result.status_code,
            "url": result.url,
            "data": result.data,
            "text": result.text,
        }
    raise ValueError(f"http_request 只支持 GET/POST，收到 {method!r}")


@register_step("assert_non_empty", description="断言列表/分页结果非空（防静默空结果）")
def _step_assert_non_empty(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    """检查接口返回的列表/总数，防止 `count` 写成 `limit` 时静默返回 0 条。

    ``path`` 支持点号路径（如 ``data.comments``）；不传时自动找
    ``data / comments / items / list`` 里的列表。
    """
    value = params.get("value")
    path = str(params.get("path", "")).strip()
    if path:
        current = value
        for part in path.split("."):
            if isinstance(current, Mapping):
                current = current.get(part)
            elif isinstance(current, list) and part.lstrip("-").isdigit():
                current = current[int(part)]
            else:
                current = None
                break
        value = current

    count = -1
    if isinstance(value, list):
        count = len(value)
    elif isinstance(value, Mapping):
        for key in ("data", "comments", "items", "list"):
            if isinstance(value.get(key), list):
                count = len(value[key])
                break
        if count < 0 and isinstance(value.get("count"), int):
            count = int(value["count"])
    elif isinstance(value, int) and not isinstance(value, bool):
        count = value
    elif value is None:
        count = 0
    else:
        count = 1

    minimum = int(params.get("min", 1))
    label = str(params.get("label", "value"))
    if count < minimum:
        raise AssertionError(
            f"静默空结果: {label} 只有 {count} 条（要求 >= {minimum}）；"
            "检查参数名（例如 count vs limit）、cookie/签名和分页游标"
        )
    return {"ok": True, "count": count, "min": minimum, "label": label}


@register_step("browser_goto", description="浏览器导航，返回导航指标")
def _step_browser_goto(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    if runtime.page is None:
        raise RuntimeError("browser_goto 需要浏览器 page")
    url = str(params["url"])
    _check_url(url, runtime.allow_hosts)
    runtime.page.goto(
        url,
        wait_until=str(params.get("wait_until", "domcontentloaded")),
        timeout=int(params.get("timeout_ms", 30000)),
    )
    metrics = dict(getattr(runtime.page, "last_nav_metrics", {}) or {})
    return {"url": url, "ready_state": metrics.get("ready_state", ""), "nav": metrics}


@register_step("browser_count", description="浏览器内统计选择器命中数")
def _step_browser_count(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    if runtime.page is None:
        raise RuntimeError("browser_count 需要浏览器 page")
    selector = str(params["selector"])
    count = int(
        runtime.page.evaluate(
            "(sel) => document.querySelectorAll(sel).length", selector
        )
        or 0
    )
    return {"selector": selector, "count": count}


@register_step("browser_wait_count", description="轮询等待选择器命中数")
def _step_browser_wait_count(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    if runtime.page is None:
        raise RuntimeError("browser_wait_count 需要浏览器 page")
    selector = str(params["selector"])
    minimum = int(params.get("min", 1))
    timeout_ms = int(params.get("timeout_ms", 15000))
    poll_ms = int(params.get("poll_ms", 400))
    started = time.perf_counter()
    deadline = started + max(100, timeout_ms) / 1000
    count = 0
    while time.perf_counter() < deadline:
        count = int(
            runtime.page.evaluate(
                "(sel) => document.querySelectorAll(sel).length", selector
            )
            or 0
        )
        if count >= minimum:
            break
        try:
            runtime.page.wait_for_timeout(poll_ms)
        except Exception:
            time.sleep(poll_ms / 1000)
    return {
        "selector": selector,
        "count": count,
        "waited_ms": int((time.perf_counter() - started) * 1000),
    }


@register_step("count_html", description="原始 HTML 选择器计数")
def _step_count_html(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    from .smoke import count_in_html

    selector = str(params["selector"])
    return {
        "selector": selector,
        "count": count_in_html(str(params.get("html", "")), selector),
    }


@register_step("classify_html", description="页面形态分类")
def _step_classify_html(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    from dataclasses import asdict

    from .page_shape import classify_page_shape

    shape = classify_page_shape(
        str(params.get("html", "")), status=int(params.get("status", 200))
    )
    return asdict(shape)


@register_step("empty_shell", description="空壳页检测")
def _step_empty_shell(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    from dataclasses import asdict

    from .empty_shell import detect_empty_shell

    result = detect_empty_shell(
        str(params.get("html", "")),
        selector=str(params.get("selector", "")),
        min_body_chars=int(params.get("min_body_chars", 200)),
    )
    return asdict(result)


@register_step("extract_state", description="提取 SSR 状态容器")
def _step_extract_state(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    from dataclasses import asdict

    from .state_extract import extract_state

    result = extract_state(str(params.get("html", "")))
    data = asdict(result) if hasattr(result, "__dataclass_fields__") else {"result": result}
    return data


@register_step("assert", description="断言：eq/ne/gte/lte/gt/lt/in/contains")
def _step_assert(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    actual = params.get("value")
    expected = params.get("expected")
    op = str(params.get("op", "eq"))
    ok = {
        "eq": lambda: actual == expected,
        "ne": lambda: actual != expected,
        "gte": lambda: actual >= expected,
        "lte": lambda: actual <= expected,
        "gt": lambda: actual > expected,
        "lt": lambda: actual < expected,
        "in": lambda: actual in expected,
        "contains": lambda: expected in actual,
    }.get(op)
    if ok is None:
        raise ValueError(f"未知断言 op: {op}")
    passed = bool(ok())
    output = {"ok": passed, "op": op, "actual": actual, "expected": expected}
    if not passed:
        raise AssertionError(f"断言失败: {actual!r} {op} {expected!r}")
    return output


@register_step("set_var", description="写入 vars")
def _step_set_var(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    name = str(params["name"])
    runtime.context.setdefault("vars", {})[name] = params.get("value")
    return {"name": name, "value": params.get("value")}


@register_step("log", description="输出一条日志")
def _step_log(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    return {"message": str(params.get("message", ""))}


@register_step("sleep", description="等待指定毫秒")
def _step_sleep(params: dict[str, Any], runtime: RecipeRuntime) -> dict[str, Any]:
    ms = int(params.get("ms", 1000))
    time.sleep(max(0, ms) / 1000)
    return {"slept_ms": ms}


# ── 执行 ─────────────────────────────────────────────

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _build_handoff(
    step: Step,
    next_index: int,
    context: Mapping[str, Any],
    *,
    reason: str = "",
    error: str = "",
) -> dict[str, Any]:
    ask = step.ask or AgentAsk(prompt=f"步骤 {step.id} 需要人工/agent 判断")
    picked: dict[str, Any] = {}
    for key in ask.context_keys:
        try:
            picked[key] = _lookup(key, context)
        except KeyError:
            picked[key] = None
    return {
        "step_id": step.id,
        "next_index": next_index,
        "prompt": ask.prompt,
        "schema": ask.schema,
        "context": picked,
        "suggestions": list(ask.suggestions),
        "reason": reason,
        "error": error,
    }


def _execute_step(step: Step, runtime: RecipeRuntime) -> Any:
    func = _STEPS.get(step.uses)
    if func is None:
        raise KeyError(
            f"未知步骤: {step.uses}；已注册: {sorted(_STEPS)}"
        )
    params = resolve_value(step.with_, runtime.context)
    return func(params, runtime)


def run_recipe(
    recipe: Recipe,
    *,
    inputs: dict[str, Any] | None = None,
    page: Any = None,
    http_client: Any = None,
    allow_hosts: Iterable[str] | None = None,
    context: dict[str, Any] | None = None,
    start_index: int = 0,
    prior_steps: list[StepRun] | None = None,
    run_id: str = "",
) -> RecipeRun:
    """执行配方；遇到 agent 检查点返回 ``needs_agent``。"""
    ctx = context or {
        "inputs": dict(inputs or {}),
        "steps": {},
        "agent": {},
        "vars": {},
    }
    hosts = tuple(allow_hosts) if allow_hosts is not None else recipe.allow_hosts
    runtime = RecipeRuntime(
        inputs=ctx.get("inputs", {}),
        context=ctx,
        page=page,
        http_client=http_client,
        allow_hosts=hosts,
    )
    run = RecipeRun(
        run_id=run_id or uuid.uuid4().hex[:12],
        recipe_id=recipe.id,
        status="done",
        context=ctx,
        steps=list(prior_steps or []),
        started_at=_now(),
        recipe_snapshot=recipe.to_dict(),
    )

    for index in range(start_index, len(recipe.steps)):
        step = recipe.steps[index]
        started = time.perf_counter()

        if step.when:
            try:
                if not eval_when(step.when, ctx):
                    run.steps.append(
                        StepRun(step.id, step.uses, "skipped", elapsed_ms=0)
                    )
                    continue
            except Exception as exc:
                run.status = "failed"
                run.error = f"when 求值失败: {type(exc).__name__}: {exc}"
                run.steps.append(
                    StepRun(step.id, step.uses, "failed", error=run.error)
                )
                run.finished_at = _now()
                return run

        if step.kind == "agent":
            run.status = "needs_agent"
            run.handoff = _build_handoff(step, index + 1, ctx)
            run.steps.append(StepRun(step.id, "agent", "needs_agent"))
            run.finished_at = _now()
            return run

        try:
            output = _execute_step(step, runtime)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            error = f"{type(exc).__name__}: {exc}"
            run.steps.append(
                StepRun(step.id, step.uses, "failed", error=error, elapsed_ms=elapsed_ms)
            )
            if step.on_error == "continue":
                continue
            if step.on_error == "agent":
                run.status = "needs_agent"
                run.handoff = _build_handoff(
                    step,
                    index + 1,
                    ctx,
                    reason="step_failed",
                    error=error,
                )
                run.finished_at = _now()
                return run
            run.status = "failed"
            run.error = f"{step.id}: {error}"
            run.finished_at = _now()
            return run

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        ctx.setdefault("steps", {})[step.output_key] = output
        ctx.setdefault("agent", {})  # 保证键存在
        run.steps.append(
            StepRun(step.id, step.uses, "ok", output=output, elapsed_ms=elapsed_ms)
        )

    run.finished_at = _now()
    return run


def resume_recipe(
    run: RecipeRun | Mapping[str, Any],
    value: Any,
    *,
    recipe: Recipe | None = None,
    page: Any = None,
    http_client: Any = None,
    allow_hosts: Iterable[str] | None = None,
) -> RecipeRun:
    """用 agent 的回答从检查点继续执行。

    ``run`` 可以是 ``RecipeRun`` 或它的 ``to_dict()`` 结果（跨进程可用）。
    ``value`` 会写入 ``context['agent'][step_id]`` 与 ``context['steps'][step_id]``。
    """
    data = run.to_dict() if isinstance(run, RecipeRun) else dict(run)
    handoff = data.get("handoff") or {}
    pending = str(handoff.get("step_id", ""))
    if not pending:
        raise ValueError("该 run 没有待处理的 agent 检查点（handoff.step_id 为空）")
    next_index = int(handoff.get("next_index", 0))

    if recipe is None:
        snapshot = data.get("recipe")
        if not snapshot:
            raise ValueError("缺少 recipe 快照；请显式传入 recipe=")
        recipe = Recipe.from_dict(snapshot)

    context = dict(data.get("context") or {})
    context.setdefault("agent", {})[pending] = value
    context.setdefault("steps", {})[pending] = value

    prior = [StepRun.from_dict(item) for item in (data.get("steps") or [])]
    # 去掉上一轮留下的 needs_agent 占位，避免同一 step 出现两行
    prior = [
        item for item in prior if not (item.id == pending and item.status == "needs_agent")
    ]
    prior.append(StepRun(pending, "agent", "ok", output=value))

    return run_recipe(
        recipe,
        context=context,
        start_index=next_index,
        prior_steps=prior,
        run_id=str(data.get("run_id", "")),
        page=page,
        http_client=http_client,
        allow_hosts=allow_hosts,
    )


# ── 加载 / 校验 ──────────────────────────────────────

def load_recipe(path: str | Path) -> Recipe:
    """从 YAML / JSON 加载配方。"""
    recipe_path = Path(path)
    text = recipe_path.read_text(encoding="utf-8-sig")
    suffix = recipe_path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover - 依赖缺失路径
            raise RuntimeError(
                "读取 YAML 配方需要 PyYAML：pip install 'anti-crawl-kit[config]'"
            ) from exc
        data = yaml.safe_load(text) or {}
    else:
        raise ValueError(f"不支持的配方格式: {recipe_path.suffix}")
    if not isinstance(data, Mapping):
        raise ValueError("配方顶层必须是映射")
    recipe = Recipe.from_dict(data)
    issues = validate_recipe(recipe)
    if issues:
        raise ValueError("配方校验失败: " + "; ".join(issues))
    return recipe


def validate_recipe(recipe: Recipe) -> list[str]:
    """返回问题列表；空列表表示通过。"""
    issues: list[str] = []
    if not recipe.id:
        issues.append("recipe.id 不能为空")
    if not recipe.steps:
        issues.append("recipe.steps 不能为空")
    seen: set[str] = set()
    for step in recipe.steps:
        if not step.id:
            issues.append("存在空 step.id")
            continue
        if step.id in seen:
            issues.append(f"step.id 重复: {step.id}")
        seen.add(step.id)
        if step.kind == "agent":
            if step.ask is None or not step.ask.prompt:
                issues.append(f"{step.id}: agent 步骤缺少 ask.prompt")
            continue
        if step.kind not in ("step", "agent"):
            issues.append(f"{step.id}: 未知 kind={step.kind!r}")
        if not step.uses:
            issues.append(f"{step.id}: 缺少 uses")
        elif step.uses not in _STEPS:
            issues.append(f"{step.id}: 未注册步骤 {step.uses!r}")
        if step.on_error not in ("stop", "continue", "agent"):
            issues.append(f"{step.id}: on_error 必须是 stop/continue/agent")
    return issues


def list_recipes(directory: str | Path) -> list[dict[str, Any]]:
    """扫描目录下的配方，返回摘要（不执行、不抛校验异常）。"""
    root = Path(directory)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*")):
        if path.suffix.lower() not in (".yaml", ".yml", ".json"):
            continue
        try:
            recipe = load_recipe(path)
        except Exception as exc:
            rows.append(
                {
                    "path": str(path),
                    "id": "",
                    "title": "",
                    "valid": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        rows.append(
            {
                "path": str(path),
                "id": recipe.id,
                "title": recipe.title,
                "description": recipe.description,
                "steps": len(recipe.steps),
                "valid": True,
                "error": "",
            }
        )
    return rows


__all__ = [
    "AgentAsk",
    "Recipe",
    "RecipeRun",
    "RecipeRuntime",
    "Step",
    "StepRun",
    "eval_when",
    "list_recipes",
    "list_steps",
    "load_recipe",
    "register_step",
    "resolve_value",
    "resume_recipe",
    "run_recipe",
    "validate_recipe",
]
