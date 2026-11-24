from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .policy import Policy
from .validate import (
    ValidationFailure,
    validate_evidence,
    validate_release,
    validate_review,
    validate_schema,
    validate_security,
)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _review_files(artifacts_dir: Path) -> list[Path]:
    return sorted(
        artifacts_dir.glob("review-*.json"),
        key=lambda path: int(path.stem.split("-")[-1]),
    )


def _count_approvals(artifacts_dir: Path) -> int:
    path = artifacts_dir / "approvals.json"
    if not path.exists():
        return 0
    data = _load_json(path)
    if isinstance(data, list):
        return len({str(item) for item in data})
    if isinstance(data, dict):
        return len({str(item) for item in data.get("approvers", [])})
    return 0


def _passed(checks: list[Check], name: str) -> bool:
    return any(check.name == name and check.passed for check in checks)


def evaluate_gate(
    artifacts_dir: Path,
    policy: Policy,
    approvals_override: int | None = None,
) -> dict[str, Any]:
    checks: list[Check] = []

    evidence_path = artifacts_dir / "evidence.json"
    if not evidence_path.exists():
        checks.append(Check("evidence", False, "evidence.json is missing"))
    else:
        try:
            validate_evidence(_load_json(evidence_path), policy)
            checks.append(Check("evidence", True, "evidence pack is complete"))
        except (ValidationFailure, json.JSONDecodeError) as exc:
            checks.append(Check("evidence", False, str(exc)))

    review_paths = _review_files(artifacts_dir)
    review_ok = False
    if not review_paths:
        checks.append(Check("review", False, "no review-*.json found"))
    else:
        previous: list[dict[str, Any]] = []
        review_detail = ""
        review_ok = True
        for path in review_paths:
            try:
                report = _load_json(path)
                validate_review(report, policy, previous)
                previous.append(report)
                review_detail = (
                    f"round {report['round']}, "
                    f"{len(report['findings'])} finding(s)"
                )
            except (ValidationFailure, json.JSONDecodeError) as exc:
                review_ok = False
                review_detail = f"{path.name}: {exc}"
                break
        checks.append(Check("review", review_ok, review_detail))

    security_path = artifacts_dir / "security.json"
    if not security_path.exists():
        checks.append(Check("security", False, "security.json is missing"))
    else:
        try:
            validate_security(_load_json(security_path), policy)
            checks.append(Check("security", True, "no blocking security findings"))
        except (ValidationFailure, json.JSONDecodeError) as exc:
            checks.append(Check("security", False, str(exc)))

    risk_path = artifacts_dir / "risk.json"
    risk_level: str | None = None
    if not risk_path.exists():
        checks.append(Check("risk", False, "risk.json is missing; run `prctl risk`"))
    else:
        try:
            risk = _load_json(risk_path)
            validate_schema(risk, "risk")
            risk_level = risk["level"]
            required_approvals = int(risk["approvals"])
            approvals = (
                approvals_override
                if approvals_override is not None
                else _count_approvals(artifacts_dir)
            )
            if approvals < required_approvals:
                checks.append(
                    Check(
                        "risk",
                        False,
                        f"{risk_level} risk needs {required_approvals} approval(s), "
                        f"got {approvals}",
                    )
                )
            else:
                checks.append(
                    Check(
                        "risk",
                        True,
                        f"{risk_level} risk, {approvals}/{required_approvals} approval(s)",
                    )
                )
        except (ValidationFailure, json.JSONDecodeError, KeyError) as exc:
            checks.append(Check("risk", False, str(exc)))

    release_path = artifacts_dir / "release.json"
    release_required = (
        risk_level in policy.release_required_for_levels if risk_level else False
    )
    if release_required and not release_path.exists():
        checks.append(Check("release", False, "release.json is required for this risk level"))
    elif release_path.exists():
        try:
            validate_release(_load_json(release_path), policy)
            checks.append(Check("release", True, "rollout and rollback plan are present"))
        except (ValidationFailure, json.JSONDecodeError) as exc:
            checks.append(Check("release", False, str(exc)))
    else:
        checks.append(Check("release", True, "not required for this risk level"))

    overall = all(check.passed for check in checks)
    if not overall:
        state = "BLOCKED"
    elif release_path.exists() and _passed(checks, "release"):
        state = "RELEASED"
    elif _passed(checks, "risk"):
        state = "RISK_APPROVED"
    elif _passed(checks, "security"):
        state = "SECURITY_CLEAR"
    elif _passed(checks, "review"):
        state = "REVIEWED"
    elif _passed(checks, "evidence"):
        state = "VERIFIED"
    else:
        state = "DRAFT"

    return {
        "overall": "PASS" if overall else "FAIL",
        "state": state,
        "checks": [asdict(check) for check in checks],
    }
