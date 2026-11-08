"""G0~G4 技术验证汇总 —— 把证据变成 Go / No-Go。

为什么需要它
------------
前面每一层都能单独跑，但项目决策需要的是一页结论：

    G0 形态：数据在哪、有没有墙
    G1 路径：哪条路可行、成本多少
    G2 数据：字段全不全、能不能去重/增量
    G3 稳定：跑 N 次稳不稳、有没有 429/验证码
    G4 工程：健康体检 + 能不能接 agent

本模块只做聚合，不重复实现探测逻辑；每个 gate 都调用现有能力，
输出 ``assessment.json`` + ``assessment.md``，给出：

    - 每关状态：PASS / WARN / FAIL / SKIP
    - 总决策：go / conditional_go / no_go / insufficient_evidence
    - 缺口清单与下一步建议

用法::

    config = load_assessment_config("examples/assessments/books.yaml")
    report = assess(config)
    assert report.decision in ("go", "conditional_go"), report.gaps
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .data_contract import (
    DataContract,
    IncrementalPolicy,
    check_incremental,
    validate_data,
)
from .dom_extract import extract_records
from .empty_shell import detect_empty_shell
from .page_shape import classify_page_shape, detect_states
from .stability import StabilityCase, run_stability

Fetch = Callable[[str], tuple[int, str, str]]

_GATE_WEIGHTS = {"G0": 20, "G1": 20, "G2": 25, "G3": 25, "G4": 10}
_GOOD_SHAPES = {"ssr", "ssr_state"}
_WALL_SHAPES = {"login_wall", "captcha", "blocked"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class AssessmentConfig:
    platform: str
    url: str
    item_selector: str = ""
    field_selectors: dict[str, dict[str, Any]] = field(default_factory=dict)
    contract: DataContract | None = None
    incremental: IncrementalPolicy = field(default_factory=IncrementalPolicy)
    runs: int = 3
    concurrency: int = 1
    interval_ms: int = 500
    min_success_rate: float = 1.0
    count_tolerance: float = 0.2
    audit_config: str = ""
    audit_only: tuple[str, ...] = ("anonymous_html", "dom")
    health_config: str = ""
    module_root: str = ""
    notes: str = ""

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        base_dir: str | Path | None = None,
    ) -> "AssessmentConfig":
        base = Path(base_dir) if base_dir else Path.cwd()

        def resolve(value: str) -> str:
            if not value:
                return ""
            path = Path(value)
            return str(path if path.is_absolute() else (base / path))

        contract_raw = data.get("contract")
        contract = (
            DataContract.from_dict(contract_raw)
            if isinstance(contract_raw, Mapping)
            else None
        )
        incremental_raw = data.get("incremental") or {}
        audit_only = data.get("audit_only") or ("anonymous_html", "dom")
        if isinstance(audit_only, str):
            audit_only = (audit_only,)
        return cls(
            platform=str(data.get("platform", "")),
            url=str(data.get("url", "")),
            item_selector=str(data.get("item_selector", "")),
            field_selectors=dict(data.get("field_selectors") or {}),
            contract=contract,
            incremental=IncrementalPolicy(
                mode=str(incremental_raw.get("mode", "full")),
                max_overlap_ratio=float(
                    incremental_raw.get("max_overlap_ratio", 1.0)
                ),
                min_new_items=int(incremental_raw.get("min_new_items", 0) or 0),
            ),
            runs=int(data.get("runs", 3) or 3),
            concurrency=int(data.get("concurrency", 1) or 1),
            interval_ms=int(data.get("interval_ms", 500) or 0),
            min_success_rate=float(data.get("min_success_rate", 1.0)),
            count_tolerance=float(data.get("count_tolerance", 0.2)),
            audit_config=resolve(str(data.get("audit_config", ""))),
            audit_only=tuple(str(item) for item in audit_only),
            health_config=resolve(str(data.get("health_config", ""))),
            module_root=resolve(str(data.get("module_root", ""))),
            notes=str(data.get("notes", "")),
        )


@dataclass
class GateResult:
    gate: str
    name: str
    status: str                     # PASS / WARN / FAIL / SKIP
    summary: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "evidence": self.evidence,
            "gaps": self.gaps,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class Assessment:
    platform: str
    url: str
    started_at: str
    finished_at: str = ""
    gates: list[GateResult] = field(default_factory=list)
    decision: str = ""
    score: int = 0
    gaps: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "url": self.url,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "decision": self.decision,
            "score": self.score,
            "gaps": self.gaps,
            "next_actions": self.next_actions,
            "gates": [gate.to_dict() for gate in self.gates],
        }

    def write(self, out_dir: str | Path) -> tuple[Path, Path]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        json_path = out / "assessment.json"
        json_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_path = out / "assessment.md"
        md_path.write_text(self.render_markdown(), encoding="utf-8")
        return json_path, md_path

    def render(self) -> str:
        lines = [
            "=" * 78,
            f"  技术验证评估  {self.platform}  {self.url}",
            f"  {self.started_at} -> {self.finished_at}",
            f"  决策: {self.decision.upper()}   评分: {self.score}/100",
            "=" * 78,
        ]
        for gate in self.gates:
            lines.append(
                f"  [{gate.status:<4}] {gate.gate} {gate.name}: {gate.summary}"
            )
        if self.gaps:
            lines.append("\n[缺口]")
            for gap in self.gaps:
                lines.append(f"  - {gap}")
        if self.next_actions:
            lines.append("\n[下一步]")
            for action in self.next_actions:
                lines.append(f"  - {action}")
        return "\n".join(lines)

    def render_markdown(self) -> str:
        lines = [
            f"# 技术验证评估 · {self.platform}",
            "",
            f"- URL: {self.url}",
            f"- 时间: {self.started_at} -> {self.finished_at}",
            f"- 决策: **{self.decision}**",
            f"- 评分: **{self.score}/100**",
            "",
            "## 关卡",
            "",
            "| Gate | 名称 | 状态 | 摘要 |",
            "|---|---|---|---|",
        ]
        for gate in self.gates:
            lines.append(
                f"| {gate.gate} | {gate.name} | {gate.status} | {gate.summary} |"
            )
        if self.gaps:
            lines += ["", "## 缺口", ""]
            lines += [f"- {gap}" for gap in self.gaps]
        if self.next_actions:
            lines += ["", "## 下一步", ""]
            lines += [f"- {action}" for action in self.next_actions]
        return "\n".join(lines) + "\n"


def _default_fetch(url: str) -> tuple[int, str, str]:
    from .http import AntiRiskHttpClient

    client = AntiRiskHttpClient("assess", timeout=25, max_retries=2)
    result = client.get_text(url)
    return result.status_code, result.text, ""


def _gate_g0(config: AssessmentConfig, fetch: Fetch) -> GateResult:
    started = datetime.now()
    try:
        status, html, error = fetch(config.url)
    except Exception as exc:
        return GateResult(
            "G0", "形态", "FAIL", f"请求失败: {type(exc).__name__}: {exc}"
        )
    if status != 200:
        return GateResult("G0", "形态", "FAIL", f"HTTP {status}", {"status": status})
    shape = classify_page_shape(html, status=status)
    empty = detect_empty_shell(html, selector=config.item_selector)
    states = detect_states(html)
    evidence = {
        "status": status,
        "bytes": len(html),
        "shape": shape.kind,
        "advice": shape.advice,
        "is_empty": empty.is_empty,
        "empty_reasons": empty.reasons,
        "states": states,
        "scripts": shape.script_count,
    }
    gaps: list[str] = []
    if shape.kind in ("captcha", "blocked"):
        return GateResult(
            "G0", "形态", "FAIL", f"命中 {shape.kind}，必须硬停", evidence,
            ["验证墙/封禁：停，不要重试"], 0,
        )
    if shape.kind == "login_wall":
        status_out = "WARN"
        gaps.append("需要登录态：准备持久化 profile + 人工登录")
    elif shape.kind in _GOOD_SHAPES and not empty.is_empty:
        status_out = "PASS"
    elif shape.kind in _GOOD_SHAPES and empty.is_empty:
        status_out = "WARN"
        if any(reason.startswith("selector_miss") for reason in empty.reasons):
            gaps.append(
                f"选择器 0 命中: {config.item_selector or '<未配置>'}（疑似改版/配置错误）"
            )
        else:
            gaps.append("页面是空壳：需要 JS/浏览器路径或接口发现")
    else:
        status_out = "WARN"
        gaps.append(f"页面形态 {shape.kind}，需要 JS/浏览器路径或接口发现")
    summary = f"shape={shape.kind} empty={empty.is_empty} states={states}"
    elapsed = int((datetime.now() - started).total_seconds() * 1000)
    return GateResult("G0", "形态", status_out, summary, evidence, gaps, elapsed)


def _gate_g1(
    config: AssessmentConfig,
    *,
    out_dir: str | Path | None = None,
) -> GateResult:
    if not config.audit_config:
        return GateResult(
            "G1", "路径", "SKIP", "未配置 path_audit",
            gaps=["路径审计未配置：补 audit YAML（14 条路径）"],
        )
    try:
        from .tools.path_audit import load_audit_config, run_audit

        audit_config = load_audit_config(config.audit_config)
        audit = run_audit(
            audit_config,
            only=list(config.audit_only),
            browser_mode="auto",
            out_dir=(Path(out_dir) / "audit") if out_dir else None,
        )
    except Exception as exc:
        return GateResult(
            "G1", "路径", "FAIL", f"审计失败: {type(exc).__name__}: {exc}"
        )

    report = audit.report()
    summary_data = report.to_dict()
    evidence = {
        "tested": report.total,
        "ok": report.ok_count,
        "success_rate": round(report.success_rate, 4),
        "status": summary_data["status"],
        "hard_stop": audit.hard_stop,
        "paths": [
            {
                "path": result.path,
                "status": result.status,
                "stop_reason": result.stop_reason,
                "latency_ms": result.latency_ms,
            }
            for result in audit.results
        ],
    }
    if audit.hard_stop:
        return GateResult(
            "G1", "路径", "FAIL", f"硬停: {audit.hard_stop}", evidence,
            ["验证墙/封禁：停，不要重试"], 0,
        )
    if report.total == 0:
        return GateResult(
            "G1", "路径", "WARN", "没有可测路径", evidence,
            ["补 audit 端点/选择器配置"],
        )
    if report.ok_count == 0:
        return GateResult(
            "G1", "路径", "FAIL", "所有已测路径失败", evidence,
            ["所有已测路径失败：换路径或检查网络/登录"],
        )
    gaps = ["单次审计不能下生产可用结论；需要双跑 + 不同时段复测"]
    return GateResult(
        "G1",
        "路径",
        "WARN",
        f"已测 {report.total} 条，成功 {report.ok_count} 条",
        evidence,
        gaps,
    )


def _gate_g2(config: AssessmentConfig, fetch: Fetch) -> GateResult:
    if config.contract is None:
        return GateResult(
            "G2", "数据", "SKIP", "未配置 data contract",
            gaps=["数据契约未配置：补字段/类型/去重/增量规则"],
        )
    if not config.item_selector or not config.field_selectors:
        return GateResult(
            "G2", "数据", "SKIP", "未配置卡片/字段选择器",
            gaps=["补 item_selector 与 field_selectors，才能验证字段完整性"],
        )

    try:
        _, html, _ = fetch(config.url)
        _, html2, _ = fetch(config.url)
    except Exception as exc:
        return GateResult(
            "G2", "数据", "FAIL", f"抓取失败: {type(exc).__name__}: {exc}"
        )

    items = extract_records(html, config.item_selector, config.field_selectors)
    items2 = extract_records(html2, config.item_selector, config.field_selectors)
    validation = validate_data(items, config.contract)
    incremental = check_incremental(
        items, items2, config.contract, config.incremental
    )
    evidence = {
        "items": validation.to_dict(),
        "incremental": incremental.to_dict(),
        "sample": items[:3],
    }
    gaps = list(validation.issues)
    if config.incremental.mode == "full":
        gaps.append("增量未验证：当前 mode=full（需要游标字段/两次真实增量样本）")
    gaps.extend(incremental.issues)
    ok = validation.ok and incremental.ok
    summary = (
        f"{validation.total} 条 / 去重后 {validation.unique} / "
        f"重复率 {validation.duplicate_ratio:.1%} / 新增 {incremental.new}"
    )
    return GateResult(
        "G2", "数据", "PASS" if ok else "FAIL", summary, evidence, gaps
    )


def _gate_g3(config: AssessmentConfig, fetch: Fetch) -> GateResult:
    case = StabilityCase(
        name=config.platform,
        url=config.url,
        item_selector=config.item_selector,
        runs=config.runs,
        concurrency=config.concurrency,
        interval_ms=config.interval_ms,
        min_success_rate=config.min_success_rate,
        count_tolerance=config.count_tolerance,
    )
    result = run_stability(case, fetch)
    summary = (
        f"{result.success}/{result.runs} 成功，"
        f"counts={result.counts}，blocked={result.blocked}"
    )
    return GateResult(
        "G3",
        "稳定",
        "PASS" if result.ok else "FAIL",
        summary,
        result.to_dict(),
        list(result.issues),
    )


def _gate_g4(config: AssessmentConfig) -> GateResult:
    import importlib.util

    mcp_ok = importlib.util.find_spec("mcp") is not None
    gaps: list[str] = []
    evidence: dict[str, Any] = {"mcp": mcp_ok}

    if not config.health_config:
        gaps.append("健康体检未配置：补 ack_health.yaml（注册表/入口/cookie/选择器）")
        if not mcp_ok:
            gaps.append("MCP 未安装：pip install 'anti-crawl-kit[mcp]'")
        return GateResult(
            "G4", "工程", "WARN", "未配置 health_config", evidence, gaps
        )

    try:
        from .health import load_health_config, run_static_health_checks

        module_root, targets = load_health_config(config.health_config)
        report = run_static_health_checks(
            targets, module_root=module_root or (config.module_root or None)
        )
    except Exception as exc:
        return GateResult(
            "G4", "工程", "FAIL", f"体检失败: {type(exc).__name__}: {exc}"
        )

    evidence["health"] = report.counts()
    if report.has_fail():
        for item in report.results:
            if item.status.value == "FAIL":
                gaps.append(f"{item.platform}: {item.detail}")
        return GateResult(
            "G4", "工程", "FAIL", f"体检 FAIL={report.counts().get('FAIL', 0)}",
            evidence, gaps,
        )
    if not mcp_ok:
        gaps.append("MCP 未安装：pip install 'anti-crawl-kit[mcp]'")
        return GateResult(
            "G4", "工程", "WARN", "体检通过，但 MCP 不可用", evidence, gaps
        )
    return GateResult(
        "G4", "工程", "PASS", "体检通过，MCP 可用", evidence, []
    )


def _decision(gates: Iterable[GateResult]) -> str:
    statuses = [gate.status for gate in gates]
    if "FAIL" in statuses:
        return "no_go"
    if statuses and all(status == "SKIP" for status in statuses):
        return "insufficient_evidence"
    if statuses and all(status == "PASS" for status in statuses):
        return "go"
    return "conditional_go"


def _score(gates: Iterable[GateResult]) -> int:
    total = 0.0
    for gate in gates:
        weight = _GATE_WEIGHTS.get(gate.gate, 0)
        if gate.status == "PASS":
            total += weight
        elif gate.status == "WARN":
            total += weight * 0.5
    return int(round(total))


_NEXT_ACTIONS = (
    ("验证墙", "停，不要重试；换 IP/时段或人工处理"),
    ("登录", "准备持久化 profile 并人工登录"),
    ("浏览器", "先 hunt_api 找接口；没有接口再走 XHR/DOM"),
    ("数据契约", "补字段/类型/去重规则（DataContract）"),
    ("增量", "提供游标字段或两次增量样本，把 mode 改为 incremental"),
    ("稳定性", "降低并发/加大间隔，观察 403/429/验证码"),
    ("健康体检", "补 ack_health.yaml，跑 ack-health"),
    ("路径审计", "补 audit YAML，跑 ack-path / path_audit"),
)


def _next_actions(gaps: Iterable[str]) -> list[str]:
    actions: list[str] = []
    for gap in gaps:
        for keyword, action in _NEXT_ACTIONS:
            if keyword in gap and action not in actions:
                actions.append(action)
    return actions


def assess(
    config: AssessmentConfig,
    *,
    fetch: Fetch | None = None,
    out_dir: str | Path | None = None,
) -> Assessment:
    """跑 G0~G4 并汇总为一份可决策报告。"""
    fetch = fetch or _default_fetch
    report = Assessment(
        platform=config.platform,
        url=config.url,
        started_at=_now(),
    )
    report.gates = [
        _gate_g0(config, fetch),
        _gate_g1(config, out_dir=out_dir),
        _gate_g2(config, fetch),
        _gate_g3(config, fetch),
        _gate_g4(config),
    ]
    report.finished_at = _now()
    report.decision = _decision(report.gates)
    report.score = _score(report.gates)
    gaps: list[str] = []
    for gate in report.gates:
        for gap in gate.gaps:
            if gap not in gaps:
                gaps.append(gap)
    report.gaps = gaps
    report.next_actions = _next_actions(gaps)
    return report


def load_assessment_config(path: str | Path) -> AssessmentConfig:
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8-sig")
    if config_path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError(
                "读取 YAML 需要 PyYAML：pip install 'anti-crawl-kit[config]'"
            ) from exc
        data = yaml.safe_load(text) or {}
    if not isinstance(data, Mapping):
        raise ValueError("评估配置顶层必须是映射")
    return AssessmentConfig.from_dict(data, base_dir=config_path.parent)


__all__ = [
    "Assessment",
    "AssessmentConfig",
    "GateResult",
    "assess",
    "load_assessment_config",
]
