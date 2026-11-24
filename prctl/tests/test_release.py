import json

import pytest

from prctl.validate import ValidationFailure, validate_release


@pytest.fixture
def service_release(examples_dir):
    return json.loads(
        (examples_dir / "release.example.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def library_release(examples_dir):
    return json.loads(
        (examples_dir / "release.library.example.json").read_text(encoding="utf-8")
    )


def test_service_release_passes(policy, service_release):
    validate_release(service_release, policy)


def test_library_release_passes(policy, library_release):
    validate_release(library_release, policy)


def test_service_requires_flag(policy, service_release):
    data = dict(service_release)
    data.pop("flag", None)
    with pytest.raises(ValidationFailure, match="flag"):
        validate_release(data, policy)


def test_library_requires_version(policy, library_release):
    data = dict(library_release)
    data.pop("version", None)
    with pytest.raises(ValidationFailure, match="version"):
        validate_release(data, policy)


def test_unknown_release_type(policy, library_release):
    data = dict(library_release)
    data["release_type"] = "desktop"
    with pytest.raises(ValidationFailure):
        validate_release(data, policy)
