"""数据契约 —— 字段完整性 / 类型 / 去重 / 增量断言。

为什么需要它（G2）
------------------
`RunReport` 只回答「拿到几条」，回答不了「字段全不全、能不能去重、增量是否生效」。
项目里 CSDN 昵称 fallback 成 id、缺失字段用 0 表示，都是「有数据但不可用」。
本模块把数据质量变成可断言的结构：

- 字段：required / type / min_len / pattern
- 去重：id_field 或 unique_by，重复率上限
- 增量：两次抓取的重叠率 / 新增条数

用法::

    contract = DataContract.from_dict({
        "id_field": "title",
        "min_items": 20,
        "fields": {
            "title": {"type": "string", "required": True, "min_len": 2},
            "price": {"type": "string", "required": True},
        },
    })
    result = validate_data(items, contract)
    assert result.ok, result.issues
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

_TYPES = {"string", "int", "float", "bool", "list", "dict", "any"}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict, tuple, set)) and len(value) == 0:
        return True
    return False


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "any":
        return True
    if expected == "string":
        return isinstance(value, str)
    if expected == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "bool":
        return isinstance(value, bool)
    if expected == "list":
        return isinstance(value, list)
    if expected == "dict":
        return isinstance(value, dict)
    return False


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str = "any"
    required: bool = True
    min_len: int = 0
    pattern: str = ""
    nullable: bool = False

    @classmethod
    def from_dict(cls, name: str, data: Mapping[str, Any] | None) -> "FieldSpec":
        data = dict(data or {})
        field_type = str(data.get("type", "any"))
        if field_type not in _TYPES:
            raise ValueError(f"字段 {name}: 不支持的类型 {field_type!r}")
        return cls(
            name=name,
            type=field_type,
            required=bool(data.get("required", True)),
            min_len=int(data.get("min_len", 0) or 0),
            pattern=str(data.get("pattern", "")),
            nullable=bool(data.get("nullable", False)),
        )


@dataclass(frozen=True)
class DataContract:
    name: str = ""
    fields: tuple[FieldSpec, ...] = ()
    id_field: str = ""
    unique_by: tuple[str, ...] = ()
    min_items: int = 0
    max_duplicate_ratio: float = 0.0
    max_missing_ratio: float = 0.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DataContract":
        raw_fields = data.get("fields") or {}
        fields: list[FieldSpec] = []
        if isinstance(raw_fields, Mapping):
            fields = [
                FieldSpec.from_dict(name, spec)
                for name, spec in raw_fields.items()
            ]
        elif isinstance(raw_fields, list):
            for item in raw_fields:
                if not isinstance(item, Mapping) or not item.get("name"):
                    raise ValueError("fields 列表项必须包含 name")
                fields.append(FieldSpec.from_dict(str(item["name"]), item))
        else:
            raise ValueError("fields 必须是映射或列表")

        unique_by = data.get("unique_by") or ()
        if isinstance(unique_by, str):
            unique_by = (unique_by,)
        return cls(
            name=str(data.get("name", "")),
            fields=tuple(fields),
            id_field=str(data.get("id_field", "")),
            unique_by=tuple(str(item) for item in unique_by),
            min_items=int(data.get("min_items", 0) or 0),
            max_duplicate_ratio=float(data.get("max_duplicate_ratio", 0.0) or 0.0),
            max_missing_ratio=float(data.get("max_missing_ratio", 0.0) or 0.0),
        )

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)

    def key_fields(self) -> tuple[str, ...]:
        if self.unique_by:
            return self.unique_by
        if self.id_field:
            return (self.id_field,)
        return self.field_names


@dataclass
class ContractResult:
    ok: bool
    total: int
    unique: int
    duplicate_ratio: float
    missing: dict[str, int] = field(default_factory=dict)
    missing_ratio: dict[str, float] = field(default_factory=dict)
    type_errors: list[dict[str, Any]] = field(default_factory=list)
    pattern_errors: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "total": self.total,
            "unique": self.unique,
            "duplicate_ratio": round(self.duplicate_ratio, 4),
            "missing": self.missing,
            "missing_ratio": {
                key: round(value, 4) for key, value in self.missing_ratio.items()
            },
            "type_errors": self.type_errors,
            "pattern_errors": self.pattern_errors,
            "issues": self.issues,
        }


def _key_for(item: Mapping[str, Any], fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(item.get(name, "")).strip() for name in fields)


def validate_data(
    items: Iterable[Mapping[str, Any]],
    contract: DataContract,
) -> ContractResult:
    """按契约校验一批记录。"""
    rows = [dict(item) for item in items]
    total = len(rows)
    issues: list[str] = []
    missing: dict[str, int] = {}
    missing_ratio: dict[str, float] = {}
    type_errors: list[dict[str, Any]] = []
    pattern_errors: list[dict[str, Any]] = []

    patterns: dict[str, re.Pattern[str]] = {}
    for spec in contract.fields:
        if spec.pattern:
            try:
                patterns[spec.name] = re.compile(spec.pattern)
            except re.error as exc:
                issues.append(f"字段 {spec.name}: 正则非法 {exc}")

    for index, row in enumerate(rows):
        for spec in contract.fields:
            value = row.get(spec.name)
            if _is_missing(value):
                if spec.required and not spec.nullable:
                    missing[spec.name] = missing.get(spec.name, 0) + 1
                continue
            if not _matches_type(value, spec.type):
                if len(type_errors) < 20:
                    type_errors.append(
                        {
                            "index": index,
                            "field": spec.name,
                            "expected": spec.type,
                            "actual": type(value).__name__,
                        }
                    )
                continue
            if spec.min_len and hasattr(value, "__len__") and len(value) < spec.min_len:
                if len(type_errors) < 20:
                    type_errors.append(
                        {
                            "index": index,
                            "field": spec.name,
                            "expected": f"len>={spec.min_len}",
                            "actual": len(value),
                        }
                    )
            pattern = patterns.get(spec.name)
            if pattern is not None and isinstance(value, str) and not pattern.search(value):
                if len(pattern_errors) < 20:
                    pattern_errors.append(
                        {"index": index, "field": spec.name, "value": value[:80]}
                    )

    if total < contract.min_items:
        issues.append(f"条目数 {total} < 要求 {contract.min_items}")

    key_fields = contract.key_fields()
    if key_fields:
        keys = [_key_for(row, key_fields) for row in rows]
        unique = len(set(keys))
    else:
        unique = total
    duplicate_ratio = (total - unique) / total if total else 0.0
    if duplicate_ratio > contract.max_duplicate_ratio:
        issues.append(
            f"重复率 {duplicate_ratio:.1%} > 上限 {contract.max_duplicate_ratio:.1%}"
        )

    for name, count in missing.items():
        ratio = count / total if total else 0.0
        missing_ratio[name] = ratio
        if ratio > contract.max_missing_ratio:
            issues.append(
                f"字段 {name} 缺失率 {ratio:.1%} > 上限 {contract.max_missing_ratio:.1%}"
            )

    if type_errors:
        issues.append(f"类型/长度错误 {len(type_errors)} 条（最多展示 20 条）")
    if pattern_errors:
        issues.append(f"正则不匹配 {len(pattern_errors)} 条（最多展示 20 条）")

    return ContractResult(
        ok=not issues,
        total=total,
        unique=unique,
        duplicate_ratio=duplicate_ratio,
        missing=missing,
        missing_ratio=missing_ratio,
        type_errors=type_errors,
        pattern_errors=pattern_errors,
        issues=issues,
    )


@dataclass(frozen=True)
class IncrementalPolicy:
    mode: str = "full"                  # full / incremental
    max_overlap_ratio: float = 1.0
    min_new_items: int = 0


@dataclass
class IncrementalResult:
    ok: bool
    previous: int
    current: int
    overlap: int
    new: int
    overlap_ratio: float
    new_ratio: float
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "previous": self.previous,
            "current": self.current,
            "overlap": self.overlap,
            "new": self.new,
            "overlap_ratio": round(self.overlap_ratio, 4),
            "new_ratio": round(self.new_ratio, 4),
            "issues": self.issues,
        }


def check_incremental(
    previous_items: Iterable[Mapping[str, Any]],
    current_items: Iterable[Mapping[str, Any]],
    contract: DataContract,
    policy: IncrementalPolicy | None = None,
) -> IncrementalResult:
    """比较两次抓取，判断增量策略是否成立。

    - ``mode=full``：允许全量重叠，只要求本次非空；
    - ``mode=incremental``：重叠率必须 <= ``max_overlap_ratio``，且新增 >= ``min_new_items``。
    """
    policy = policy or IncrementalPolicy()
    key_fields = contract.key_fields()
    previous = [_key_for(dict(row), key_fields) for row in previous_items]
    current = [_key_for(dict(row), key_fields) for row in current_items]
    previous_set = set(previous)
    current_set = set(current)
    overlap = len(current_set & previous_set)
    new = len(current_set - previous_set)
    current_count = len(current_set)
    overlap_ratio = overlap / current_count if current_count else 0.0
    new_ratio = new / current_count if current_count else 0.0

    issues: list[str] = []
    if current_count == 0:
        issues.append("本次抓取为空")
    if policy.mode not in ("full", "incremental"):
        issues.append(f"未知 mode: {policy.mode}")
    elif policy.mode == "incremental":
        if overlap_ratio > policy.max_overlap_ratio:
            issues.append(
                f"重叠率 {overlap_ratio:.1%} > 上限 {policy.max_overlap_ratio:.1%}"
                "（增量游标可能未生效）"
            )
        if new < policy.min_new_items:
            issues.append(f"新增 {new} < 要求 {policy.min_new_items}")

    return IncrementalResult(
        ok=not issues,
        previous=len(previous_set),
        current=current_count,
        overlap=overlap,
        new=new,
        overlap_ratio=overlap_ratio,
        new_ratio=new_ratio,
        issues=issues,
    )


__all__ = [
    "ContractResult",
    "DataContract",
    "FieldSpec",
    "IncrementalPolicy",
    "IncrementalResult",
    "check_incremental",
    "validate_data",
]
