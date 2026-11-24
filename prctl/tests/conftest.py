from pathlib import Path

import pytest

from prctl.policy import Policy


@pytest.fixture
def policy(tmp_path: Path) -> Policy:
    return Policy.load(tmp_path / "missing-policy.yml")


@pytest.fixture
def examples_dir() -> Path:
    return Path(__file__).parent.parent / "examples"
