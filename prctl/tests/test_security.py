import pytest

from prctl.validate import ValidationFailure, validate_security


def report(status="open", severity="high", conclusion="pass"):
    return {
        "pr": "123",
        "conclusion": conclusion,
        "findings": [
            {
                "id": "S-001",
                "severity": severity,
                "category": "authz",
                "location": "src/auth.py:10",
                "problem": "Missing permission check",
                "evidence": "Handler has no authz guard",
                "fix": "Add the guard",
                "status": status,
                "disposition_note": (
                    "Accepted by security lead" if status == "accepted" else ""
                ),
            }
        ],
    }


def test_open_high_blocks(policy):
    with pytest.raises(ValidationFailure, match="open high"):
        validate_security(report(status="open", severity="high"), policy)


def test_fixed_passes(policy):
    validate_security(report(status="fixed"), policy)


def test_accepted_requires_note(policy):
    data = report(status="accepted")
    data["findings"][0]["disposition_note"] = ""
    with pytest.raises(ValidationFailure, match="requires disposition_note"):
        validate_security(data, policy)


def test_conclusion_must_pass(policy):
    with pytest.raises(ValidationFailure, match="conclusion"):
        validate_security(report(conclusion="fail"), policy)
