import pytest

from prctl.validate import ValidationFailure, validate_review


def make_review(round=1, context="fresh", conclusion="pass", findings=None):
    return {
        "pr": "123",
        "round": round,
        "context": context,
        "conclusion": conclusion,
        "findings": findings or [],
    }


def finding(**overrides):
    data = {
        "id": "R-001",
        "severity": "P2",
        "category": "impact",
        "file": "src/app.py",
        "line": 10,
        "problem": "Something is off",
        "evidence": "src/app.py:10",
        "disposition": "fixed",
        "disposition_note": "Fixed",
    }
    data.update(overrides)
    return data


def test_valid_review(policy):
    validate_review(make_review(), policy)


def test_round_limit(policy):
    previous = [
        make_review(round=1),
        make_review(round=2, context="delta"),
        make_review(round=3, context="delta"),
    ]
    with pytest.raises(ValidationFailure, match="exceeds max"):
        validate_review(make_review(round=4, context="delta"), policy, previous)


def test_delta_required(policy):
    previous = [make_review(round=1)]
    with pytest.raises(ValidationFailure, match="context=delta"):
        validate_review(make_review(round=2, context="fresh"), policy, previous)


def test_p0_must_be_fixed(policy):
    with pytest.raises(ValidationFailure, match="must be fixed"):
        validate_review(
            make_review(findings=[finding(severity="P0", disposition="explained")]),
            policy,
        )


def test_non_fixed_requires_note(policy):
    with pytest.raises(ValidationFailure, match="requires disposition_note"):
        validate_review(
            make_review(
                findings=[finding(disposition="deferred", disposition_note="")]
            ),
            policy,
        )


def test_duplicate_id_across_rounds(policy):
    previous = [make_review(round=1, findings=[finding()])]
    with pytest.raises(ValidationFailure, match="already raised"):
        validate_review(
            make_review(round=2, context="delta", findings=[finding()]),
            policy,
            previous,
        )


def test_category_must_be_allowed(policy):
    with pytest.raises(ValidationFailure, match="category"):
        validate_review(
            make_review(findings=[finding(category="style")]),
            policy,
        )


def test_conclusion_must_pass(policy):
    with pytest.raises(ValidationFailure, match="conclusion"):
        validate_review(make_review(conclusion="changes_requested"), policy)
