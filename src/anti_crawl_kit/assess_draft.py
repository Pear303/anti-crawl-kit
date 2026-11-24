"""从 DOM 探测结果生成 G0~G4 评估配置骨架。

这条链路解决的是「Agent 拿到探测结果后仍要手写 YAML」的断层：

    dom_probe(url)  →  item_selector / field_selectors 候选
    draft_assessment(probe)  →  assessment.yaml + audit.yaml
    ack-assess --config assessment.yaml  →  Go / No-Go

生成的选择器全部来自真实 DOM，不接受调用方凭空填写；调用方只能覆盖
``item_selector`` / ``fields`` 这类「命名」决策，不能覆盖「事实」。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

# 字段命名顺序：title/url 单独处理，其余按这个顺序兜底
_NAME_ORDER = ("name", "price", "stock", "meta", "extra")

_PRICE_RE = re.compile(r"^[£$¥€]\s*\d|\d+[.,]\d{2}\s*$")
_STOCK_WORDS = ("in stock", "out of stock", "库存", "有货", "无货", "现货", "缺货")

# 容器选择器里出现这些词，更像「一条数据」而不是「一整块布局」
_ITEM_HINTS = (
    "item",
    "card",
    "product",
    "note",
    "post",
    "article",
    "entry",
    "list-item",
    "row",
)


def _q(value: str) -> str:
    """YAML 双引号字符串；用 JSON 转义，YAML 1.2 与 JSON 兼容。"""
    return json.dumps(str(value), ensure_ascii=False)


def _platform_from_url(url: str) -> str:
    match = re.match(r"https?://([^/:]+)", url or "")
    host = (match.group(1) if match else "draft").lower()
    host = host.removeprefix("www.")
    parts = [p for p in host.split(".") if p]
    # books.toscrape.com → books（子域通常才是平台名）；example.com → example
    if parts:
        return re.sub(r"[^a-z0-9_-]+", "-", parts[0]) or "draft"
    return "draft"


def _score_candidate(item: Mapping[str, Any]) -> int:
    """给列表容器候选打分：像「一条数据」的优先，整块布局靠后。"""
    selector = str(item.get("selector", "")).lower()
    score = 0
    if any(hint in selector for hint in _ITEM_HINTS):
        score += 3
    if selector.startswith(("article", "li")):
        score += 2
    count = int(item.get("count", 0) or 0)
    if 3 <= count <= 200:
        score += 2
    outline = item.get("outline") or []
    if any(node.get("href") for node in outline):
        score += 3
    text_fields = [n for n in outline if (n.get("text") or "").strip()]
    if 1 <= len(text_fields) <= 8:
        score += 1
    sample = str(item.get("sample", ""))
    if len(sample) < 5:
        score -= 2
    return score


def pick_item_selector(probe: Mapping[str, Any]) -> str:
    """从探测结果里挑一个最像列表项的容器选择器。"""
    candidates = probe.get("item_candidates") or probe.get("dom_candidates") or []
    best = ""
    best_score = -10**9
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        score = _score_candidate(item)
        if score > best_score:
            best, best_score = str(item.get("selector", "")), score
    return best


def _candidate_outline(probe: Mapping[str, Any], selector: str) -> list[Mapping[str, Any]]:
    for item in probe.get("item_candidates") or []:
        if isinstance(item, Mapping) and item.get("selector") == selector:
            return [n for n in (item.get("outline") or []) if isinstance(n, Mapping)]
    return []


def _guess_field_name(text: str, used: set[str], fallback) -> str:
    """按内容猜字段名：能确定的（价格/库存）才猜，猜不出用中性名。"""
    lowered = text.lower()
    if "price" not in used and _PRICE_RE.search(text):
        return "price"
    if "stock" not in used and (
        lowered in _STOCK_WORDS or any(word in lowered for word in _STOCK_WORDS)
    ):
        return "stock"
    return fallback()


def pick_fields(
    probe: Mapping[str, Any],
    item_selector: str,
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """从容器内部结构挑字段：优先「短文本 + 链接」，其次纯短文本。"""
    outline = _candidate_outline(probe, item_selector)
    fields: list[dict[str, Any]] = []
    used: set[str] = set()
    # 父节点和子节点常常同文本（h3 > a）：记住文本，避免重复产出字段
    text_owner: dict[str, str] = {}

    def next_name() -> str:
        for name in _NAME_ORDER:
            if name not in used:
                return name
        index = 2
        while f"field{index}" in used:
            index += 1
        return f"field{index}"

    for node in outline:
        if len(fields) >= limit:
            break
        selector = str(node.get("selector", "")).strip()
        text = str(node.get("text", "")).strip()
        href = str(node.get("href", "")).strip()
        if not selector:
            continue
        if not text or len(text) > 60:
            continue

        if text in text_owner:
            # 同一段文本已经作为字段出现过：只可能补一个 url
            if href and "url" not in used and len(fields) < limit:
                fields.append({"name": "url", "selector": selector, "attr": "href"})
                used.add("url")
            continue

        # 第一个短文本当主字段 title；带链接时顺带产出 url
        if "title" not in used:
            fields.append({"name": "title", "selector": selector})
            used.add("title")
            text_owner[text] = "title"
            if href and "url" not in used and len(fields) < limit:
                fields.append({"name": "url", "selector": selector, "attr": "href"})
                used.add("url")
            continue

        name = _guess_field_name(text, used, next_name)
        if name in used:
            continue
        # 非主字段只收短文本：父容器常把「价格 + 库存 + 按钮」拼成一句，
        # 收进来会把真正的叶子选择器挤掉。
        if len(text) > 24:
            continue
        fields.append({"name": name, "selector": selector})
        used.add(name)
        text_owner[text] = name

    return fields


def _field_yaml(fields: Iterable[Mapping[str, Any]], indent: str = "  ") -> list[str]:
    lines: list[str] = []
    for item in fields:
        lines.append(f"{indent}{item['name']}:")
        lines.append(f"{indent}  selector: {_q(item['selector'])}")
        if item.get("attr"):
            lines.append(f"{indent}  attr: {_q(item['attr'])}")
    return lines


def _contract_yaml(fields: list[dict[str, Any]], min_items: int) -> list[str]:
    lines = ["contract:"]
    lines.append("  max_duplicate_ratio: 0.0")
    lines.append("  max_missing_ratio: 0.2")
    lines.append(f"  min_items: {min_items}")
    if not fields:
        lines.append("  fields: {}")
        return lines
    lines.append("  fields:")
    for item in fields:
        required = "true" if item["name"] == "title" else "false"
        lines.append(f"    {item['name']}:")
        lines.append('      type: "string"')
        lines.append(f"      required: {required}")
        if item["name"] == "title":
            lines.append("      min_len: 1")
    return lines


def draft_assessment(
    probe: Mapping[str, Any],
    *,
    platform: str = "",
    item_selector: str = "",
    fields: list[str] | None = None,
    runs: int = 3,
    link_audit: bool = True,
    audit_name: str = "",
) -> dict[str, Any]:
    """把 ``probe_dom`` 的结果转成评估配置。

    返回 ``config_yaml`` / ``audit_yaml`` / ``warnings``；调用方负责落盘。
    """
    url = str(probe.get("url", "")).strip()
    if not url:
        raise ValueError("probe 结果缺少 url，无法生成评估配置")

    platform = platform or _platform_from_url(url)
    item = item_selector or pick_item_selector(probe)
    warnings: list[str] = []

    if not item:
        warnings.append("没有找到可用的列表容器选择器；请手工指定 item_selector")
        item = ""

    picked = pick_fields(probe, item) if item else []
    if not picked:
        warnings.append("没有找到字段选择器候选；contract 只做条数与去重校验")
    if fields:
        # 只保留调用方点名的字段，顺序按探测结果
        wanted = {str(name) for name in fields}
        picked = [f for f in picked if f["name"] in wanted]
        if not picked:
            warnings.append(f"指定的字段 {sorted(wanted)} 都不在探测候选里")

    count = 0
    for candidate in probe.get("item_candidates") or []:
        if isinstance(candidate, Mapping) and candidate.get("selector") == item:
            count = int(candidate.get("count", 0) or 0)
            break
    min_items = max(1, count // 2) if count else 1

    audit_name = audit_name or f"{platform}_audit.yaml"
    lines = [
        "# 由 anti-crawl-kit 生成（来源：DOM 探测结果）",
        "# 重新生成：ack init assess --from-probe <probe.json>",
        "# 注意：选择器来自真实 DOM；字段名与类型请确认后再用于生产结论。",
        "",
        f"platform: {_q(platform)}",
        f"url: {_q(url)}",
        f"notes: {_q('DOM 探测自动生成；字段语义待确认')}",
        "",
        f"item_selector: {_q(item)}",
        "",
        "field_selectors:",
    ]
    lines.extend(_field_yaml(picked))
    if not picked:
        lines[-1] = "field_selectors: {}"
    lines.append("")
    lines.extend(_contract_yaml(picked, min_items))
    lines.extend(
        [
            "",
            "incremental:",
            '  mode: "full"',
            "  max_overlap_ratio: 1.0",
            "",
            f"runs: {max(1, int(runs))}",
            "concurrency: 1",
            "interval_ms: 500",
            "min_success_rate: 1.0",
            "count_tolerance: 0.2",
            "",
            "# G1：指向自动生成的审计配置；留空则跳过路径审计",
            f"audit_config: {_q(audit_name if link_audit else '')}",
            'health_config: ""',
            'module_root: ""',
            "",
        ]
    )

    audit_lines: list[str] = [
        "# 由 anti-crawl-kit 生成（来源：DOM 探测结果）",
        f"platform: {_q(platform)}",
        f"test_url: {_q(url)}",
        "skip_version_check: true",
        f"card_selector: {_q(item)}",
        "dom_fields:",
    ]
    for field in picked:
        audit_lines.append(f"  {field['name']}:")
        audit_lines.append(f"    selector: {_q(field['selector'])}")
        audit_lines.append(f"    attr: {_q(field.get('attr') or 'text')}")
    audit_lines.append("field_semantics:")
    for field in picked:
        audit_lines.append(f"  {field['name']}: {_q('待确认')}")
    audit_lines.append(f"notes: {_q('自动生成，仅供技术验证')}")
    audit_lines.append("")

    return {
        "ok": True,
        "tool": "draft_assessment",
        "platform": platform,
        "url": url,
        "item_selector": item,
        "fields": [dict(f) for f in picked],
        "item_count": count,
        "min_items": min_items,
        "config_yaml": "\n".join(lines),
        "audit_yaml": "\n".join(audit_lines),
        "audit_name": audit_name,
        "warnings": warnings,
        "next_actions": [
            "确认字段名与类型（title/url/price...）",
            "保存 config_yaml 后跑 ack-assess --config <file>",
            "G1 会验证匿名访问与 DOM 两条路径；失败按缺口提示处理",
        ],
    }


def write_draft(result: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    """把草稿写到目录，返回文件路径。"""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    platform = str(result.get("platform") or "draft")
    config_path = target / f"{platform}_assessment.yaml"
    config_path.write_text(str(result["config_yaml"]), encoding="utf-8")

    audit_path = ""
    if result.get("audit_yaml"):
        audit_file = target / str(result.get("audit_name") or f"{platform}_audit.yaml")
        audit_file.write_text(str(result["audit_yaml"]), encoding="utf-8")
        audit_path = str(audit_file)

    return {"config_path": str(config_path), "audit_path": audit_path}


__all__ = [
    "draft_assessment",
    "pick_fields",
    "pick_item_selector",
    "write_draft",
]
