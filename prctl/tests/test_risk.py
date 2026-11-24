from prctl.risk import classify


def test_high_risk_wins(policy):
    result = classify(["README.md", "billing/calculator.py"], policy)
    assert result["level"] == "high"
    assert result["approvals"] == 2
    assert any(item["level"] == "high" for item in result["matched"])


def test_low_risk(policy):
    result = classify(["README.md", "docs/guide.md"], policy)
    assert result["level"] == "low"
    assert result["approvals"] == 0


def test_unknown_is_medium(policy):
    result = classify(["scripts/release.sh"], policy)
    assert result["level"] == "medium"
    assert result["approvals"] == 1
