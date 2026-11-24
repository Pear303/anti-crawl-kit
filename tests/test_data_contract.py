"""数据契约测试：字段 / 类型 / 去重 / 增量。"""

from __future__ import annotations

from anti_crawl_kit.data_contract import (
    DataContract,
    IncrementalPolicy,
    check_incremental,
    validate_data,
)


def _contract(**overrides) -> DataContract:
    data = {
        "name": "demo",
        "id_field": "id",
        "min_items": 2,
        "max_duplicate_ratio": 0.0,
        "max_missing_ratio": 0.0,
        "fields": {
            "id": {"type": "string", "required": True},
            "title": {"type": "string", "required": True, "min_len": 2},
            "price": {"type": "float", "required": True},
        },
    }
    data.update(overrides)
    return DataContract.from_dict(data)


def test_validate_pass() -> None:
    result = validate_data(
        [
            {"id": "1", "title": "AB", "price": 1.5},
            {"id": "2", "title": "CD", "price": 2},
        ],
        _contract(),
    )
    assert result.ok
    assert result.total == 2
    assert result.unique == 2
    assert result.duplicate_ratio == 0


def test_validate_missing_required() -> None:
    result = validate_data(
        [
            {"id": "1", "title": "AB", "price": 1.5},
            {"id": "2", "title": "", "price": 2},
        ],
        _contract(),
    )
    assert not result.ok
    assert result.missing["title"] == 1
    assert any("缺失率" in issue for issue in result.issues)


def test_validate_type_error() -> None:
    result = validate_data(
        [
            {"id": "1", "title": "AB", "price": "not-a-number"},
            {"id": "2", "title": "CD", "price": 2},
        ],
        _contract(),
    )
    assert not result.ok
    assert result.type_errors[0]["field"] == "price"


def test_validate_min_len_and_pattern() -> None:
    contract = _contract(
        fields={
            "id": {"type": "string"},
            "title": {"type": "string", "min_len": 5, "pattern": "^Book"},
            "price": {"type": "float"},
        }
    )
    result = validate_data(
        [
            {"id": "1", "title": "AB", "price": 1.5},
            {"id": "2", "title": "Book B", "price": 2},
        ],
        contract,
    )
    assert not result.ok
    assert result.type_errors  # min_len
    assert result.pattern_errors  # "^Book"


def test_min_len_skips_values_without_len() -> None:
    """没有 __len__ 的字段不做长度检查（int / float）。"""
    contract = _contract(
        id_field="",
        min_items=1,
        max_duplicate_ratio=1.0,
        fields={"likes": {"type": "any", "min_len": 5}},
    )
    result = validate_data([{"likes": 12}, {"likes": 3.5}], contract)
    assert result.ok


def test_min_len_tolerates_dynamic_len_objects() -> None:
    """动态 __getattr__ 提供 __len__ 的脏值不能让整批校验抛 TypeError。"""

    class DynamicLen:
        def __getattr__(self, name: str):
            if name == "__len__":
                return lambda: 1
            raise AttributeError(name)

    contract = _contract(
        id_field="",
        min_items=1,
        fields={"payload": {"type": "any", "min_len": 3}},
    )
    result = validate_data([{"payload": DynamicLen()}], contract)
    assert result.ok


def test_validate_duplicate_ratio() -> None:
    result = validate_data(
        [
            {"id": "1", "title": "AB", "price": 1.5},
            {"id": "1", "title": "AB", "price": 1.5},
        ],
        _contract(),
    )
    assert not result.ok
    assert result.duplicate_ratio == 0.5


def test_validate_min_items() -> None:
    result = validate_data([{"id": "1", "title": "AB", "price": 1.0}], _contract())
    assert not result.ok
    assert any("条目数" in issue for issue in result.issues)


def test_incremental_full_mode_allows_overlap() -> None:
    items = [{"id": "1", "title": "AB", "price": 1.0}]
    result = check_incremental(items, items, _contract(min_items=0))
    assert result.ok
    assert result.overlap_ratio == 1.0


def test_incremental_mode_rejects_full_overlap() -> None:
    items = [
        {"id": "1", "title": "AB", "price": 1.0},
        {"id": "2", "title": "CD", "price": 2.0},
    ]
    result = check_incremental(
        items,
        items,
        _contract(min_items=0),
        IncrementalPolicy(mode="incremental", max_overlap_ratio=0.5),
    )
    assert not result.ok
    assert any("重叠率" in issue for issue in result.issues)


def test_incremental_mode_accepts_new_items() -> None:
    previous = [{"id": "1", "title": "AB", "price": 1.0}]
    current = [{"id": "2", "title": "CD", "price": 2.0}]
    result = check_incremental(
        previous,
        current,
        _contract(min_items=0),
        IncrementalPolicy(mode="incremental", max_overlap_ratio=0.5, min_new_items=1),
    )
    assert result.ok
    assert result.new == 1
    assert result.overlap == 0


def test_contract_from_list_fields() -> None:
    contract = DataContract.from_dict(
        {"fields": [{"name": "id", "type": "string"}]}
    )
    assert contract.field_names == ("id",)
