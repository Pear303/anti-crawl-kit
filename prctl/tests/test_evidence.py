import copy
import json

import pytest

from prctl.validate import ValidationFailure, validate_evidence


@pytest.fixture
def evidence(examples_dir):
    return json.loads(
        (examples_dir / "evidence.example.json").read_text(encoding="utf-8")
    )


def test_valid_evidence(policy, evidence):
    validate_evidence(evidence, policy)


def test_missing_rollback(policy, evidence):
    data = copy.deepcopy(evidence)
    del data["rollback"]
    with pytest.raises(ValidationFailure):
        validate_evidence(data, policy)


def test_fail_result_blocks(policy, evidence):
    data = copy.deepcopy(evidence)
    data["acceptance_criteria"][0]["result"] = "FAIL"
    with pytest.raises(ValidationFailure, match="gate requires PASS"):
        validate_evidence(data, policy)


def test_missing_screenshot_blocks(policy, evidence):
    data = copy.deepcopy(evidence)
    data["run_evidence"]["screenshots"] = []
    with pytest.raises(ValidationFailure, match="screenshots"):
        validate_evidence(data, policy)


def test_no_ui_change_allows_no_screenshot(policy, evidence):
    data = copy.deepcopy(evidence)
    data["ui_change"] = False
    data["no_ui_reason"] = "Library-only change; no user interface."
    data["run_evidence"]["screenshots"] = []
    validate_evidence(data, policy)


def test_no_ui_change_requires_reason(policy, evidence):
    data = copy.deepcopy(evidence)
    data["ui_change"] = False
    data["run_evidence"]["screenshots"] = []
    with pytest.raises(ValidationFailure, match="no_ui_reason"):
        validate_evidence(data, policy)
