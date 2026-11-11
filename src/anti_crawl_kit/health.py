"""平台健康体检 —— 把「改版静默失效」从事故变成例行检查。

为什么需要它
------------
项目最后一次补的脚本就是 ``platform_health_check.py``。在此之前，平台改版
靠用户投诉发现：主选择器 0 命中、兜底撑着；cookie 名单过时导致永远判未登录；
新增平台时 ``runner.py`` 入口提取漏分支 → 任务「成功」但 0 条。

本模块把体检做成**配置驱动**的两层：

A. 静态检查（零浏览器，秒级）
    - 模块文件是否存在
    - 类名是否逐字出现在源码里
    - 入口参数（``entry_param``）是否在采集器源码出现
    - 认证 cookie 名单是否在源码出现（防 Python/JS 两处漂移）
    - 二维码选择器是否在登录源码出现
    - 探针选择器是否仍存在于源码（清单过期 → 结论不可信）

B. 动态探针（可选，需浏览器）
    - 打开 ``probe_url``，统计选择器 / 全局状态对象命中数
    - 0 命中 → FAIL（疑似改版）；验证码/登录跳转 → WARN

四档状态：``OK / WARN / FAIL / SKIP``；``fail > 0`` 退出码 1，可直接接 CI。

用法::

    from anti_crawl_kit.health import HealthTarget, run_static_health_checks

    targets = [
        HealthTarget(
            platform="bilibili",
            collector_module="mycollectors.bilibili",
            collector_class="BilibiliCollector",
            auth_cookies=("SESSDATA",),
            entry_param="uid",
            probe_selectors=(".bili-video-card",),
        )
    ]
    report = run_static_health_checks(targets, module_root="src")
    print(report.render())
    raise SystemExit(1 if report.has_fail() else 0)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from ._compat import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping


class HealthStatus(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True)
class HealthTarget:
    """单个平台的体检目标（配置驱动，不绑定具体框架）。"""

    platform: str
    label: str = ""

    # ── 静态检查 ──
    collector_module: str = ""
    collector_class: str = ""
    login_module: str = ""
    login_class: str = ""
    cookie_module: str = ""          # 认证 cookie 名单所在模块（可选）
    auth_cookies: tuple[str, ...] = ()
    entry_param: str = ""            # uid / user_id / sec_uid / url
    qrcode_selector: str = ""
    needs_login: bool = False

    # ── 动态探针 ──
    probe_url: str = ""
    probe_selectors: tuple[str, ...] = ()
    probe_state_names: tuple[str, ...] = ()
    probe_file: str = ""             # 探针清单对应的源码文件（默认取 collector_module）

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HealthTarget":
        def _tuple(key: str) -> tuple[str, ...]:
            value = data.get(key, ())
            if isinstance(value, str):
                return (value,)
            return tuple(str(item) for item in (value or ()) if item)

        return cls(
            platform=str(data.get("platform", "")).strip(),
            label=str(data.get("label", "")),
            collector_module=str(data.get("collector_module", "")),
            collector_class=str(data.get("collector_class", "")),
            login_module=str(data.get("login_module", "")),
            login_class=str(data.get("login_class", "")),
            cookie_module=str(data.get("cookie_module", "")),
            auth_cookies=_tuple("auth_cookies"),
            entry_param=str(data.get("entry_param", "")),
            qrcode_selector=str(data.get("qrcode_selector", "")),
            needs_login=bool(data.get("needs_login", False)),
            probe_url=str(data.get("probe_url", "")),
            probe_selectors=_tuple("probe_selectors"),
            probe_state_names=_tuple("probe_state_names"),
            probe_file=str(data.get("probe_file", "")),
        )


@dataclass(frozen=True)
class CheckResult:
    dimension: str
    platform: str
    status: HealthStatus
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthReport:
    started_at: str = ""
    finished_at: str = ""
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def counts(self) -> dict[str, int]:
        out = {status.value: 0 for status in HealthStatus}
        for result in self.results:
            key = str(result.status)
            out[key] = out.get(key, 0) + 1
        return out

    def has_fail(self) -> bool:
        return any(result.status == HealthStatus.FAIL for result in self.results)

    def has_warn(self) -> bool:
        return any(result.status == HealthStatus.WARN for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.counts(),
            "results": [
                {
                    "dimension": result.dimension,
                    "platform": result.platform,
                    "status": str(result.status),
                    "detail": result.detail,
                    "extra": result.extra,
                }
                for result in self.results
            ],
        }

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def render(self) -> str:
        counts = self.counts()
        lines = [
            "=" * 72,
            f"  平台健康体检  {self.started_at} -> {self.finished_at}",
            (
                f"  汇总: OK={counts.get('OK', 0)}  WARN={counts.get('WARN', 0)}  "
                f"FAIL={counts.get('FAIL', 0)}  SKIP={counts.get('SKIP', 0)}"
            ),
            "=" * 72,
        ]
        labels = {
            "registry": "A. 注册表完整性",
            "entry": "B. 入口参数",
            "login": "C. 登录判据",
            "probe_drift": "D. 探针清单漂移",
            "probe": "E. 动态探针",
        }
        marks = {
            HealthStatus.OK: "[OK]  ",
            HealthStatus.WARN: "[WARN]",
            HealthStatus.FAIL: "[FAIL]",
            HealthStatus.SKIP: "[SKIP]",
        }
        current = None
        for result in self.results:
            if result.dimension != current:
                current = result.dimension
                lines.append(f"\n--- {labels.get(current, current)} ---")
            lines.append(
                f"  {marks.get(result.status, str(result.status))} "
                f"{result.platform:<16} {result.detail}"
            )
        lines.append("")
        if self.has_fail():
            lines.append("[结论] 存在 FAIL 项 —— 按维度排查（模块/类名/选择器/名单漂移）")
        elif self.has_warn():
            lines.append("[结论] 无 FAIL，存在 WARN 项 —— 建议人工复核归因")
        else:
            lines.append("[结论] 全部通过")
        return "\n".join(lines)


# ── 源码定位 ─────────────────────────────────────────

def _read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace").replace('\\"', '"')
    except OSError:
        return ""


def _module_candidates(module_root: Path, module: str) -> list[Path]:
    """把模块名转成候选文件路径（兼容 module_root 是 src 或包目录）。"""
    if not module:
        return []
    if module.endswith(".py") or "/" in module or "\\" in module:
        return [module_root / module]
    parts = [part for part in module.split(".") if part]
    candidates = [module_root.joinpath(*parts).with_suffix(".py")]
    if parts and parts[0] == module_root.name and len(parts) > 1:
        candidates.append(module_root.joinpath(*parts[1:]).with_suffix(".py"))
    return candidates


def _locate_module(module_root: Path, module: str) -> tuple[Path | None, list[Path]]:
    candidates = _module_candidates(module_root, module)
    for candidate in candidates:
        if candidate.is_file():
            return candidate, candidates
    return None, candidates


def _rel(module_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(module_root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _source_bundle(module_root: Path, modules: Iterable[str]) -> str:
    parts: list[str] = []
    for module in modules:
        if not module:
            continue
        path, _ = _locate_module(module_root, module)
        if path is not None:
            parts.append(_read_source(path))
    return "\n".join(parts)


# ── 静态检查 ─────────────────────────────────────────

def check_duplicates(targets: Iterable[HealthTarget]) -> list[CheckResult]:
    seen: dict[str, int] = {}
    for target in targets:
        seen[target.platform] = seen.get(target.platform, 0) + 1
    return [
        CheckResult(
            "registry",
            platform,
            HealthStatus.FAIL,
            f"平台重复注册 {count} 次（单一来源被破坏）",
        )
        for platform, count in seen.items()
        if count > 1
    ]


def check_target(
    target: HealthTarget,
    *,
    module_root: str | Path | None,
) -> list[CheckResult]:
    """单个平台的静态检查。"""
    results: list[CheckResult] = []
    if not target.platform:
        return [
            CheckResult("registry", "-", HealthStatus.FAIL, "platform 为空")
        ]
    if module_root is None:
        return [
            CheckResult(
                "registry",
                target.platform,
                HealthStatus.SKIP,
                "未提供 module_root，跳过静态检查",
            )
        ]

    root = Path(module_root)
    if not root.is_dir():
        return [
            CheckResult(
                "registry",
                target.platform,
                HealthStatus.FAIL,
                f"module_root 不存在: {root}",
            )
        ]

    # A. 采集器模块 + 类名
    collector_src = ""
    if not target.collector_module and not target.collector_class:
        results.append(
            CheckResult("registry", target.platform, HealthStatus.SKIP, "未配置采集器")
        )
    else:
        if not target.collector_module:
            results.append(
                CheckResult(
                    "registry", target.platform, HealthStatus.FAIL, "有 collector_class 但缺 collector_module"
                )
            )
        else:
            path, candidates = _locate_module(root, target.collector_module)
            if path is None:
                results.append(
                    CheckResult(
                        "registry",
                        target.platform,
                        HealthStatus.FAIL,
                        f"采集器模块文件缺失: {target.collector_module} "
                        f"(候选: {', '.join(_rel(root, c) for c in candidates)})",
                    )
                )
            else:
                collector_src = _read_source(path)
                results.append(
                    CheckResult(
                        "registry",
                        target.platform,
                        HealthStatus.OK,
                        f"模块存在: {_rel(root, path)}",
                    )
                )
                if not target.collector_class:
                    results.append(
                        CheckResult(
                            "registry",
                            target.platform,
                            HealthStatus.FAIL,
                            "有 collector_module 但缺 collector_class",
                        )
                    )
                elif target.collector_class not in collector_src:
                    results.append(
                        CheckResult(
                            "registry",
                            target.platform,
                            HealthStatus.FAIL,
                            f"类名 {target.collector_class} 不在 {_rel(root, path)} 中",
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            "registry",
                            target.platform,
                            HealthStatus.OK,
                            f"类名存在: {target.collector_class}",
                        )
                    )

    # B. 入口参数（项目最贵的教训：runner 漏分支 → 全空）
    if not target.entry_param:
        results.append(
            CheckResult(
                "entry",
                target.platform,
                HealthStatus.WARN,
                "未声明 entry_param（新增平台时容易漏入口提取分支）",
            )
        )
    elif not collector_src:
        results.append(
            CheckResult(
                "entry",
                target.platform,
                HealthStatus.SKIP,
                f"无法读取采集器源码，跳过 entry_param={target.entry_param} 校验",
            )
        )
    elif target.entry_param not in collector_src:
        results.append(
            CheckResult(
                "entry",
                target.platform,
                HealthStatus.FAIL,
                f"entry_param={target.entry_param!r} 不在采集器源码中（入口提取可能漏分支）",
            )
        )
    else:
        results.append(
            CheckResult(
                "entry",
                target.platform,
                HealthStatus.OK,
                f"entry_param 存在: {target.entry_param}",
            )
        )

    # C. 登录判据：认证 cookie + 二维码选择器
    login_src = _source_bundle(root, [target.login_module])
    cookie_src = _source_bundle(root, [target.cookie_module]) or collector_src
    combined = "\n".join([collector_src, login_src, cookie_src])

    if target.auth_cookies:
        missing = [name for name in target.auth_cookies if name not in combined]
        if len(missing) == len(target.auth_cookies):
            results.append(
                CheckResult(
                    "login",
                    target.platform,
                    HealthStatus.FAIL,
                    f"认证 cookie 名单未在源码出现: {list(target.auth_cookies)}（名单漂移）",
                )
            )
        elif missing:
            results.append(
                CheckResult(
                    "login",
                    target.platform,
                    HealthStatus.WARN,
                    f"部分认证 cookie 未在源码出现: {missing}",
                )
            )
        else:
            results.append(
                CheckResult(
                    "login",
                    target.platform,
                    HealthStatus.OK,
                    f"认证 cookie 名单存在: {list(target.auth_cookies)}",
                )
            )
    elif target.needs_login:
        results.append(
            CheckResult(
                "login",
                target.platform,
                HealthStatus.WARN,
                "needs_login=True 但未声明 auth_cookies（登录判定可能永远失败）",
            )
        )

    if target.qrcode_selector:
        if not target.login_module:
            results.append(
                CheckResult(
                    "login",
                    target.platform,
                    HealthStatus.WARN,
                    "声明了 qrcode_selector 但未配置 login_module，无法校验",
                )
            )
        elif target.qrcode_selector not in login_src:
            results.append(
                CheckResult(
                    "login",
                    target.platform,
                    HealthStatus.FAIL,
                    f"二维码选择器不在登录源码中: {target.qrcode_selector!r}",
                )
            )
        else:
            results.append(
                CheckResult(
                    "login",
                    target.platform,
                    HealthStatus.OK,
                    "二维码选择器存在",
                )
            )

    # D. 探针清单漂移：清单里的选择器必须仍存在于源码
    if target.probe_selectors:
        probe_module = target.probe_file or target.collector_module
        probe_src = _source_bundle(root, [probe_module]) if probe_module else ""
        if not probe_src:
            results.append(
                CheckResult(
                    "probe_drift",
                    target.platform,
                    HealthStatus.SKIP,
                    "无法读取探针源码，跳过漂移检查",
                )
            )
        else:
            stale = [sel for sel in target.probe_selectors if sel not in probe_src]
            if len(stale) == len(target.probe_selectors):
                results.append(
                    CheckResult(
                        "probe_drift",
                        target.platform,
                        HealthStatus.FAIL,
                        f"探针选择器全部不在源码中（清单已过期）: {stale}",
                    )
                )
            elif stale:
                results.append(
                    CheckResult(
                        "probe_drift",
                        target.platform,
                        HealthStatus.WARN,
                        f"部分探针选择器不在源码中: {stale}",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        "probe_drift",
                        target.platform,
                        HealthStatus.OK,
                        "探针选择器全部仍在源码中",
                    )
                )
    return results


def run_static_health_checks(
    targets: Iterable[HealthTarget],
    *,
    module_root: str | Path | None = None,
) -> HealthReport:
    """跑全部静态检查（零浏览器）。"""
    target_list = list(targets)
    report = HealthReport(started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    for result in check_duplicates(target_list):
        report.add(result)
    for target in target_list:
        for result in check_target(target, module_root=module_root):
            report.add(result)
    report.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return report


# ── 动态探针 ─────────────────────────────────────────

_COUNT_JS = """
(sels) => {
  const out = {};
  for (const sel of sels) {
    try { out[sel] = document.querySelectorAll(sel).length; }
    catch (e) { out[sel] = -1; }
  }
  return out;
}
"""

_STATE_JS = """
(names) => {
  const out = {};
  for (const name of names) {
    try {
      let cur = window;
      for (const part of name.split('.')) { cur = cur ? cur[part] : undefined; }
      out[name] = cur !== undefined && cur !== null;
    } catch (e) { out[name] = false; }
  }
  return out;
}
"""


def _evaluate(page: Any, script: str, arg: Any = None) -> Any:
    """优先主世界执行；不支持 isolated_context 的替身自动回退。"""
    try:
        if arg is None:
            return page.evaluate(script, isolated_context=False)
        return page.evaluate(script, arg, isolated_context=False)
    except TypeError:
        if arg is None:
            return page.evaluate(script)
        return page.evaluate(script, arg)


def _classify_page(page: Any, url: str) -> str:
    try:
        from .captcha import detect_captcha_page

        if detect_captcha_page(page).detected:
            return "captcha"
    except Exception:
        pass
    lowered = (url or "").lower()
    if any(keyword in lowered for keyword in ("login", "signin", "passport", "auth")):
        return "login"
    if any(keyword in lowered for keyword in ("captcha", "verify", "challenge")):
        return "captcha"
    return "ok"


def probe_health_page(
    page: Any,
    target: HealthTarget,
    *,
    wait_ms: int = 3000,
    timeout: int = 30000,
) -> CheckResult:
    """免登录动态探针：打开 ``probe_url``，统计选择器/状态对象命中。"""
    if not target.probe_url:
        return CheckResult(
            "probe", target.platform, HealthStatus.SKIP, "未配置 probe_url"
        )
    if not target.probe_selectors and not target.probe_state_names:
        return CheckResult(
            "probe", target.platform, HealthStatus.SKIP, "未配置探针选择器"
        )

    started = time.perf_counter()
    try:
        page.goto(target.probe_url, wait_until="domcontentloaded", timeout=timeout)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)
        counts = (
            dict(_evaluate(page, _COUNT_JS, list(target.probe_selectors)) or {})
            if target.probe_selectors
            else {}
        )
        states = (
            dict(_evaluate(page, _STATE_JS, list(target.probe_state_names)) or {})
            if target.probe_state_names
            else {}
        )
        try:
            url_after = str(_evaluate(page, "() => location.href") or "")
        except Exception:
            url_after = target.probe_url
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return CheckResult(
            "probe",
            target.platform,
            HealthStatus.WARN,
            f"页面打开失败: {type(exc).__name__}: {str(exc)[:160]}",
            {"url": target.probe_url, "ms": elapsed_ms},
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    hits = sum(value for value in counts.values() if isinstance(value, int) and value > 0)
    hits += sum(1 for value in states.values() if value)
    page_state = _classify_page(page, url_after)
    detail = (
        f"counts={counts} states={states} url_state={page_state}"
    )
    extra = {"url": target.probe_url, "ms": elapsed_ms, "counts": counts, "states": states}

    if page_state == "captcha":
        return CheckResult(
            "probe", target.platform, HealthStatus.WARN, f"命中验证码/风控页: {detail}", extra
        )
    if page_state == "login":
        return CheckResult(
            "probe", target.platform, HealthStatus.WARN, f"跳转登录（需登录态复测）: {detail}", extra
        )
    if hits <= 0:
        return CheckResult(
            "probe",
            target.platform,
            HealthStatus.FAIL,
            f"选择器/状态对象全部未命中 —— 疑似页面改版: {detail}",
            extra,
        )
    return CheckResult(
        "probe", target.platform, HealthStatus.OK, f"命中 {hits}: {detail}", extra
    )


# ── 配置加载 ─────────────────────────────────────────

def load_health_config(path: str | Path) -> tuple[Path | None, list[HealthTarget]]:
    """读取 YAML/JSON 体检配置，返回 ``(module_root, targets)``。

    配置示例::

        module_root: ../src
        platforms:
          - platform: bilibili
            collector_module: mypkg.collectors.bilibili
            collector_class: BilibiliCollector
            auth_cookies: [SESSDATA]
            entry_param: uid
            probe_url: https://space.bilibili.com/2
            probe_selectors: [".bili-video-card"]
    """
    config_path = Path(path)
    # utf-8-sig：兼容 Windows 编辑器/PowerShell 写入的 BOM。
    text = config_path.read_text(encoding="utf-8-sig")
    suffix = config_path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover - 依赖缺失路径
            raise RuntimeError(
                "读取 YAML 配置需要 PyYAML：pip install 'anti-crawl-kit[config]'"
            ) from exc
        data = yaml.safe_load(text) or {}
    else:
        raise ValueError(f"不支持的配置格式: {config_path.suffix}（仅 .yaml/.yml/.json）")

    if not isinstance(data, dict):
        raise ValueError("体检配置顶层必须是映射（mapping）")

    raw_root = data.get("module_root")
    module_root = None
    if raw_root:
        module_root = Path(str(raw_root))
        if not module_root.is_absolute():
            module_root = (config_path.parent / module_root).resolve()

    targets = [
        HealthTarget.from_dict(item)
        for item in (data.get("platforms") or [])
        if isinstance(item, dict)
    ]
    return module_root, targets


__all__ = [
    "CheckResult",
    "HealthReport",
    "HealthStatus",
    "HealthTarget",
    "check_duplicates",
    "check_target",
    "load_health_config",
    "probe_health_page",
    "run_static_health_checks",
]
