from __future__ import annotations

import fnmatch
from collections.abc import Iterable

from .policy import Policy

LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}


def matches(path: str, patterns: Iterable[str]) -> list[str]:
    return [pattern for pattern in patterns if fnmatch.fnmatch(path, pattern)]


def classify(files: list[str], policy: Policy) -> dict:
    rules = policy.risk_rules
    level = "low"
    matched: list[dict] = []
    for file_path in files:
        high_hits = matches(file_path, rules.get("high", []))
        if high_hits:
            level = "high"
            matched.append({"file": file_path, "level": "high", "patterns": high_hits})
            continue
        low_hits = matches(file_path, rules.get("low", []))
        if low_hits:
            matched.append({"file": file_path, "level": "low", "patterns": low_hits})
            continue
        if LEVEL_ORDER[level] < LEVEL_ORDER["medium"]:
            level = "medium"
        matched.append({"file": file_path, "level": "medium", "patterns": []})
    approvals = policy.approvals
    return {
        "level": level,
        "approvals": int(approvals.get(level, 0)),
        "matched": matched,
    }
