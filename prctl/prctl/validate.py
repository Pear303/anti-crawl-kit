from __future__ import annotations

import json
from importlib import resources
from typing import Any

from jsonschema import Draft7Validator

from .policy import Policy


class ValidationFailure(Exception):
    """Raised when an artifact does not satisfy the policy."""


def load_schema(name: str) -> dict[str, Any]:
    root = resources.files("prctl")
    schema_path = (
        root.joinpath("assets")
        .joinpath("schemas")
        .joinpath(f"{name}.schema.json")
    )
    with schema_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_schema(instance: dict[str, Any], name: str) -> None:
    schema = load_schema(name)
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda error: str(error.path))
    if errors:
        messages = []
        for error in errors:
            location = ".".join(str(part) for part in error.path) or "<root>"
            messages.append(f"{location}: {error.message}")
        raise ValidationFailure(
            f"{name} schema validation failed:\n- " + "\n- ".join(messages)
        )


def validate_evidence(evidence: dict[str, Any], policy: Policy) -> None:
    validate_schema(evidence, "evidence")
    ui_change = evidence.get("ui_change", True)
    if (
        policy.require_screenshots
        and ui_change
        and not evidence["run_evidence"]["screenshots"]
    ):
        raise ValidationFailure(
            "evidence.run_evidence.screenshots must contain at least one screenshot "
            "for a UI change"
        )
    if not ui_change and not evidence.get("no_ui_reason"):
        raise ValidationFailure(
            "evidence.no_ui_reason is required when ui_change is false"
        )
    for index, item in enumerate(evidence["acceptance_criteria"], start=1):
        if item["result"] != "PASS":
            raise ValidationFailure(
                f"acceptance criterion {index} is {item['result']}; gate requires PASS"
            )
    if not evidence["rollback"]["steps"]:
        raise ValidationFailure("evidence.rollback.steps must not be empty")


def validate_review(
    review: dict[str, Any],
    policy: Policy,
    previous_reviews: list[dict[str, Any]] | None = None,
) -> None:
    validate_schema(review, "review")
    previous_reviews = previous_reviews or []
    max_rounds = policy.review_max_rounds
    expected_round = len(previous_reviews) + 1
    if review["round"] != expected_round:
        raise ValidationFailure(
            f"review round must be {expected_round}, got {review['round']}; "
            "rounds must be sequential"
        )
    if review["round"] > max_rounds:
        raise ValidationFailure(
            f"review round {review['round']} exceeds max {max_rounds}; escalate to a human"
        )
    if review["round"] > 1 and review["context"] != "delta":
        raise ValidationFailure(
            "review round > 1 must use context=delta; only review the new changes"
        )
    if review["conclusion"] != "pass":
        raise ValidationFailure(
            f"review conclusion is {review['conclusion']}; fix findings and re-review"
        )
    seen_ids = {
        finding["id"]
        for previous in previous_reviews
        for finding in previous.get("findings", [])
    }
    current_ids: set[str] = set()
    for finding in review["findings"]:
        if finding["category"] not in policy.review_categories:
            raise ValidationFailure(
                f"finding {finding['id']} category {finding['category']} is not allowed"
            )
        if finding["id"] in current_ids:
            raise ValidationFailure(
                f"duplicate finding id {finding['id']} in this report"
            )
        current_ids.add(finding["id"])
        if finding["id"] in seen_ids:
            raise ValidationFailure(
                f"finding id {finding['id']} was already raised in a previous round; "
                "do not repeat unchanged findings"
            )
        if (
            finding["severity"] in policy.blocking_severities
            and finding["disposition"] != "fixed"
        ):
            raise ValidationFailure(
                f"blocking finding {finding['id']} ({finding['severity']}) must be fixed, "
                f"not {finding['disposition']}"
            )
        if finding["disposition"] != "fixed" and not finding.get("disposition_note"):
            raise ValidationFailure(
                f"finding {finding['id']} with disposition {finding['disposition']} "
                "requires disposition_note"
            )


def validate_security(security: dict[str, Any], policy: Policy) -> None:
    validate_schema(security, "security")
    if security["conclusion"] != "pass":
        raise ValidationFailure(
            f"security conclusion is {security['conclusion']}; resolve findings first"
        )
    blocking = {"critical", "high", "medium"}
    for finding in security["findings"]:
        status = finding.get("status", "open")
        if status == "open" and finding["severity"] in blocking:
            raise ValidationFailure(
                f"open {finding['severity']} security finding {finding['id']} "
                f"at {finding['location']}"
            )
        if status == "accepted" and not finding.get("disposition_note"):
            raise ValidationFailure(
                f"accepted security finding {finding['id']} requires disposition_note"
            )


def validate_release(release: dict[str, Any], policy: Policy) -> None:
    validate_schema(release, "release")
    release_type = release.get("release_type", "service")
    if release_type in {"service", "cli"}:
        if policy.release_require_flag and not release.get("flag"):
            raise ValidationFailure(
                f"release.flag is required for {release_type} releases"
            )
    elif release_type == "library":
        if not release.get("version"):
            raise ValidationFailure("release.version is required for library releases")
    if policy.release_require_rollback and not release["rollback"]["steps"]:
        raise ValidationFailure("release.rollback.steps is required")
    if len(release["rollout"]) < policy.release_min_rollout_stages:
        raise ValidationFailure(
            f"release.rollout needs at least {policy.release_min_rollout_stages} stage(s)"
        )
