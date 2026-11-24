from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_POLICY_PATH = Path(".prctl/policy.yml")

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "review": {
        "max_rounds": 3,
        "categories": ["goal", "compatibility", "impact"],
        "noise_categories": ["style", "naming", "redundancy", "formatting"],
        "require_delta_after_round": 1,
        "blocking_severities": ["P0"],
    },
    "evidence": {
        "require_screenshots": True,
        "require_commands": True,
        "require_rollback": True,
    },
    "risk": {
        "default_level": "medium",
        "approvals": {"low": 0, "medium": 1, "high": 2},
        "rules": {
            "high": [
                "billing/**",
                "payments/**",
                "auth/**",
                "security/**",
                "migrations/**",
                "infra/**",
                "deploy/**",
                ".github/workflows/**",
                "**/Dockerfile",
                "**/*.tf",
            ],
            "medium": ["src/**", "packages/**", "apps/**", "services/**"],
            "low": [
                "docs/**",
                "*.md",
                "**/*.md",
                "tests/**",
                "**/*.test.*",
                "**/*.spec.*",
            ],
        },
    },
    "release": {
        "required_for_levels": ["medium", "high"],
        "require_flag": True,
        "require_rollback": True,
        "min_rollout_stages": 1,
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into a deep copy of base; lists are replaced, dicts merged."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


@dataclass(frozen=True)
class Policy:
    data: dict[str, Any]
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "Policy":
        policy_path = path or DEFAULT_POLICY_PATH
        if policy_path.exists():
            with policy_path.open(encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"policy must be a mapping: {policy_path}")
            return cls(deep_merge(DEFAULTS, loaded), policy_path)
        return cls(copy.deepcopy(DEFAULTS), None)

    def _section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        if not isinstance(value, dict):
            raise ValueError(f"policy section '{name}' must be a mapping")
        return value

    @property
    def review_max_rounds(self) -> int:
        return int(self._section("review")["max_rounds"])

    @property
    def review_categories(self) -> list[str]:
        return list(self._section("review")["categories"])

    @property
    def blocking_severities(self) -> list[str]:
        return list(self._section("review")["blocking_severities"])

    @property
    def require_screenshots(self) -> bool:
        return bool(self._section("evidence")["require_screenshots"])

    @property
    def approvals(self) -> dict[str, int]:
        return dict(self._section("risk")["approvals"])

    @property
    def risk_rules(self) -> dict[str, list[str]]:
        return {key: list(value) for key, value in self._section("risk")["rules"].items()}

    @property
    def default_level(self) -> str:
        return str(self._section("risk")["default_level"])

    @property
    def release_required_for_levels(self) -> list[str]:
        return list(self._section("release")["required_for_levels"])

    @property
    def release_require_flag(self) -> bool:
        return bool(self._section("release")["require_flag"])

    @property
    def release_require_rollback(self) -> bool:
        return bool(self._section("release")["require_rollback"])

    @property
    def release_min_rollout_stages(self) -> int:
        return int(self._section("release")["min_rollout_stages"])
