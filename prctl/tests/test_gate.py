import json

from prctl.gate import evaluate_gate


def load(examples_dir, name):
    return json.loads((examples_dir / name).read_text(encoding="utf-8"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def setup_full(tmp_path, examples_dir, risk_level="high"):
    write(tmp_path / "evidence.json", load(examples_dir, "evidence.example.json"))
    write(tmp_path / "review-1.json", load(examples_dir, "review.example.json"))
    write(tmp_path / "security.json", load(examples_dir, "security.example.json"))
    risk = load(examples_dir, "risk.example.json")
    if risk_level == "low":
        risk = {"level": "low", "approvals": 0, "matched": []}
    write(tmp_path / "risk.json", risk)
    write(tmp_path / "release.json", load(examples_dir, "release.example.json"))


def test_happy_path(policy, tmp_path, examples_dir):
    setup_full(tmp_path, examples_dir)
    result = evaluate_gate(tmp_path, policy, approvals_override=2)
    assert result["overall"] == "PASS"
    assert result["state"] == "RELEASED"


def test_missing_evidence(policy, tmp_path, examples_dir):
    setup_full(tmp_path, examples_dir)
    (tmp_path / "evidence.json").unlink()
    result = evaluate_gate(tmp_path, policy, approvals_override=2)
    assert result["overall"] == "FAIL"
    assert any(
        check["name"] == "evidence" and not check["passed"]
        for check in result["checks"]
    )


def test_insufficient_approvals(policy, tmp_path, examples_dir):
    setup_full(tmp_path, examples_dir)
    result = evaluate_gate(tmp_path, policy, approvals_override=1)
    assert result["overall"] == "FAIL"
    assert any(
        check["name"] == "risk" and not check["passed"]
        for check in result["checks"]
    )


def test_high_risk_requires_release(policy, tmp_path, examples_dir):
    setup_full(tmp_path, examples_dir)
    (tmp_path / "release.json").unlink()
    result = evaluate_gate(tmp_path, policy, approvals_override=2)
    assert result["overall"] == "FAIL"
    assert any(
        check["name"] == "release" and not check["passed"]
        for check in result["checks"]
    )


def test_low_risk_no_release(policy, tmp_path, examples_dir):
    setup_full(tmp_path, examples_dir, risk_level="low")
    (tmp_path / "release.json").unlink()
    result = evaluate_gate(tmp_path, policy, approvals_override=0)
    assert result["overall"] == "PASS"
    assert result["state"] == "RISK_APPROVED"
