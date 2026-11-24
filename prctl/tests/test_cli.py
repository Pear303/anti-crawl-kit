import json

from prctl.cli import main


def test_review_same_round_is_idempotent(tmp_path, examples_dir, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--dir", str(tmp_path)]) == 0
    report = examples_dir / "review.example.json"
    assert main(["review", "--pr", "1", "--report", str(report)]) == 0
    # Re-validating the same round should overwrite, not expect round 2.
    assert main(["review", "--pr", "1", "--report", str(report)]) == 0


def test_init_risk_and_gate(tmp_path, examples_dir, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / ".prctl" / "policy.yml").exists()
    assert (tmp_path / ".prctl" / "schemas" / "evidence.schema.json").exists()

    assert main(["risk", "--pr", "123", "--files", "billing/calculator.py"]) == 0
    risk_path = tmp_path / ".prctl" / "artifacts" / "123" / "risk.json"
    assert risk_path.exists()
    risk = json.loads(risk_path.read_text(encoding="utf-8"))
    assert risk["level"] == "high"

    # Gate fails before the artifacts exist.
    assert main(["gate", "--pr", "123"]) == 1

    assert (
        main(
            [
                "verify",
                "--pr",
                "123",
                "--evidence",
                str(examples_dir / "evidence.example.json"),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "review",
                "--pr",
                "123",
                "--report",
                str(examples_dir / "review.example.json"),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "security",
                "--pr",
                "123",
                "--report",
                str(examples_dir / "security.example.json"),
            ]
        )
        == 0
    )
    assert main(["approve", "--pr", "123", "--by", "alice"]) == 0
    assert main(["approve", "--pr", "123", "--by", "bob"]) == 0
    assert (
        main(
            [
                "release",
                "--pr",
                "123",
                "--plan",
                str(examples_dir / "release.example.json"),
            ]
        )
        == 0
    )
    assert main(["gate", "--pr", "123"]) == 0
