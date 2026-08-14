from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import pytest

import agentrunproof.history.bundle as bundle_module
from agentrunproof.history.bundle import (
    BUNDLE_SCHEMA_VERSION,
    ENVIRONMENT_SCHEMA_VERSION,
    HistoryBundleError,
    file_member,
    finalize_history_bundle,
    history_bundle_json,
    limitations,
    load_history_bundle,
    validate_history_bundle,
    write_history_bundle,
)

_SDK_VERSIONS = ("0.19.2", "0.19.3", "0.19.4", "0.20.0")
_MATRIX_ID = f"sha256:{'b' * 64}"
_WHEEL_NAME = "agentrunproof-0.1.0-py3-none-any.whl"
_WHEEL_SHA = "c" * 64


def _environment(version: str) -> dict[str, Any]:
    filename = f"openai_agents-{version}-py3-none-any.whl"
    return {
        "schema_version": ENVIRONMENT_SCHEMA_VERSION,
        "sdk_version": version,
        "python": "3.12.13",
        "pip_version": "25.0.1",
        "dependency_metadata_bypassed": version != "0.20.0",
        "lock_sha256": hashlib.sha256(version.encode()).hexdigest(),
        "artifacts": [
            {
                "distribution": "openai-agents",
                "version": version,
                "filename": filename,
                "size": 123,
                "sha256": hashlib.sha256(filename.encode()).hexdigest(),
            }
        ],
    }


def _draft(directory: Path) -> dict[str, Any]:
    matrix_member = file_member(directory / "matrix.json")
    matrix_member["matrix_id"] = _MATRIX_ID
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source": {
            "commit": "1" * 40,
            "tree": "2" * 40,
            "clean": True,
            "index_flags_clean": True,
        },
        "runtime": {
            "python": "3.12.13",
            "implementation": "CPython",
            "system": "Linux",
            "machine": "x86_64",
            "fresh_venvs": True,
            "python_isolated": True,
            "network_guard": "socket-deny-v1",
            "worker_cwd": "empty-temporary-directory",
            "environment_sanitized": True,
            "worker_timeout_seconds": 120,
        },
        "wheel": {
            "path": _WHEEL_NAME,
            "size": 456,
            "sha256": _WHEEL_SHA,
        },
        "environments": [_environment(version) for version in _SDK_VERSIONS],
        "matrix": matrix_member,
        "limitations": limitations(),
    }


@pytest.fixture
def evidence_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "evidence"
    directory.mkdir()
    (directory / "matrix.json").write_text('{"synthetic":true}\n', encoding="utf-8")

    def parse_matrix(_text: str) -> dict[str, Any]:
        return {
            "matrix_id": _MATRIX_ID,
            "wheel": {"name": _WHEEL_NAME, "sha256": _WHEEL_SHA},
            "runs": [{"sdk_version": version} for version in _SDK_VERSIONS],
        }

    monkeypatch.setattr(bundle_module, "parse_history_matrix_json", parse_matrix)
    return directory


def test_bundle_round_trips_and_marker_is_written_last(
    evidence_directory: Path,
) -> None:
    bundle = finalize_history_bundle(_draft(evidence_directory), directory=evidence_directory)
    assert bundle["bundle_id"].startswith("sha256:")
    assert validate_history_bundle(bundle, directory=evidence_directory) == bundle

    marker = evidence_directory / "bundle.json"
    write_history_bundle(marker, bundle)
    assert load_history_bundle(marker) == bundle
    assert history_bundle_json(bundle, directory=evidence_directory).endswith("\n")
    assert marker.stat().st_mode & 0o777 == 0o600


def test_matrix_tamper_and_partial_publication_are_rejected(evidence_directory: Path) -> None:
    bundle = finalize_history_bundle(_draft(evidence_directory), directory=evidence_directory)
    (evidence_directory / "matrix.json").write_text('{"tampered":true}\n', encoding="utf-8")

    with pytest.raises(HistoryBundleError, match="matrix.size|matrix.sha256"):
        validate_history_bundle(bundle, directory=evidence_directory)

    (evidence_directory / "matrix.json").unlink()
    with pytest.raises(HistoryBundleError, match="missing"):
        validate_history_bundle(bundle, directory=evidence_directory)


def test_optional_ci_wheel_is_verified_when_present(
    evidence_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft = _draft(evidence_directory)
    wheel = evidence_directory / _WHEEL_NAME
    wheel.write_bytes(b"canonical-wheel")
    draft["wheel"] = file_member(wheel)
    wheel_digest = draft["wheel"]["sha256"]

    def parse_matrix(_text: str) -> dict[str, Any]:
        return {
            "matrix_id": _MATRIX_ID,
            "wheel": {"name": _WHEEL_NAME, "sha256": wheel_digest},
            "runs": [{"sdk_version": version} for version in _SDK_VERSIONS],
        }

    monkeypatch.setattr(bundle_module, "parse_history_matrix_json", parse_matrix)
    bundle = finalize_history_bundle(draft, directory=evidence_directory)

    wheel.write_bytes(b"tampered-wheel")
    with pytest.raises(HistoryBundleError, match="wheel.size|wheel.sha256"):
        validate_history_bundle(bundle, directory=evidence_directory)


def test_environment_sdk_tamper_is_rejected_even_after_readdressing(
    evidence_directory: Path,
) -> None:
    bundle = finalize_history_bundle(_draft(evidence_directory), directory=evidence_directory)
    forged = copy.deepcopy(bundle)
    environment = forged["environments"][0]
    environment["artifacts"][0]["version"] = "9.9.9"
    environment["environment_id"] = bundle_module._content_id(
        environment,
        "environment_id",
    )
    forged["bundle_id"] = bundle_module._content_id(forged, "bundle_id")

    with pytest.raises(HistoryBundleError, match="exact openai-agents wheel"):
        validate_history_bundle(forged, directory=evidence_directory)


def test_loader_rejects_duplicate_keys_and_non_finite_numbers(
    evidence_directory: Path,
) -> None:
    marker = evidence_directory / "bundle.json"
    marker.write_text('{"bundle_id":"first","bundle_id":"second"}', encoding="utf-8")
    with pytest.raises(HistoryBundleError, match="Duplicate JSON object key"):
        load_history_bundle(marker)

    marker.write_text('{"value":NaN}', encoding="utf-8")
    with pytest.raises(HistoryBundleError, match="Non-finite JSON number"):
        load_history_bundle(marker)
