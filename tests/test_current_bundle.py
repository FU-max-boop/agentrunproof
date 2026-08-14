from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

import agentrunproof.certificate as certificate_module
from agentrunproof._canonical import JsonValue, sha256_hex
from agentrunproof._version import __version__
from agentrunproof.builtins import RUNSTATE_SIBLING_APPROVAL_ISOLATION
from agentrunproof.certificate import build_certificate, write_certificate
from agentrunproof.cli import main
from agentrunproof.current.bundle import (
    BUNDLE_SCHEMA_VERSION,
    ENVIRONMENT_ID,
    CurrentBundleError,
    finalize_current_bundle,
    finalize_current_certificate,
    limitations,
    load_current_bundle,
    validate_current_bundle,
    validate_current_certificate,
    write_current_bundle,
)
from agentrunproof.engine import run_scenario
from agentrunproof.history.bundle import file_member, load_history_bundle

ROOT = Path(__file__).resolve().parents[1]
HISTORY_BUNDLE = ROOT / "evidence" / "history" / "v1" / "bundle.json"
SOURCE_COMMIT = "1" * 40
SOURCE_TREE = "2" * 40


@pytest.mark.asyncio
async def test_current_bundle_binds_exact_failure_certificate_and_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle_path, bundle = await _write_bundle(tmp_path)

    assert load_current_bundle(bundle_path) == bundle
    assert main(["check-current-bundle", str(bundle_path)]) == 0
    assert f"VALID {bundle['bundle_id']} current-bundle" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_current_certificate_rejects_generic_but_noncanonical_runtime(
    tmp_path: Path,
) -> None:
    bundle_path, _ = await _write_bundle(tmp_path)
    certificate_path = bundle_path.with_name("certificate.json")
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    certificate["runtime"]["platform"] = {"system": "Darwin", "machine": "arm64"}
    certificate["certificate_id"] = certificate_module._certificate_id(certificate)

    certificate_module.validate_certificate(certificate)
    with pytest.raises(CurrentBundleError, match="runtime.platform"):
        validate_current_certificate(certificate)


@pytest.mark.asyncio
async def test_current_bundle_rejects_payload_replacement_and_unknown_closure(
    tmp_path: Path,
) -> None:
    bundle_path, bundle = await _write_bundle(tmp_path)
    certificate_path = bundle_path.with_name("certificate.json")
    certificate_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CurrentBundleError, match="certificate.size|certificate.sha256"):
        load_current_bundle(bundle_path)

    _, replacement = await _write_bundle(tmp_path / "replacement")
    forged = copy.deepcopy(replacement)
    environment = forged["environment"]
    assert isinstance(environment, dict)
    environment["environment_id"] = "sha256:" + "0" * 64
    forged["bundle_id"] = None
    forged["bundle_id"] = _bundle_id(forged)
    with pytest.raises(CurrentBundleError, match="environment_id"):
        validate_current_bundle(forged, directory=tmp_path / "replacement")


@pytest.mark.asyncio
async def test_current_bundle_rejects_duplicate_manifest_keys(tmp_path: Path) -> None:
    bundle_path, bundle = await _write_bundle(tmp_path)
    rendered = bundle_path.read_text(encoding="utf-8")
    duplicate = rendered.replace(
        '  "schema_version": "agentrunproof.current-bundle/v1",',
        '  "schema_version": "agentrunproof.current-bundle/v1",\n'
        '  "schema_version": "agentrunproof.current-bundle/v1",',
        1,
    )
    bundle_path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(CurrentBundleError, match="Duplicate JSON object key"):
        load_current_bundle(bundle_path)
    assert bundle["schema_version"] == BUNDLE_SCHEMA_VERSION


async def _write_bundle(directory: Path) -> tuple[Path, dict[str, JsonValue]]:
    directory.mkdir(parents=True, exist_ok=True)
    certificate = build_certificate(await run_scenario(RUNSTATE_SIBLING_APPROVAL_ISOLATION))
    certificate["runtime"] = {
        "python": "3.12.13",
        "implementation": "CPython",
        "platform": {"system": "Linux", "machine": "x86_64"},
        "packages": {
            "openai-agents": "0.20.0",
            "openai": "2.54.0",
            "pydantic": "2.13.4",
        },
    }
    certificate["source"] = {
        "commit": None,
        "dirty": None,
        "tracked_diff_sha256": None,
        "untracked_paths_sha256": None,
        "index_flags_sha256": None,
    }
    certificate["certificate_id"] = certificate_module._certificate_id(certificate)
    certificate = finalize_current_certificate(certificate, source_commit=SOURCE_COMMIT)

    certificate_path = directory / "certificate.json"
    write_certificate(certificate_path, certificate)
    certificate_member = file_member(certificate_path)
    certificate_member["certificate_id"] = certificate["certificate_id"]
    environment = _history_environment()
    bundle = finalize_current_bundle(
        {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "source": {
                "commit": SOURCE_COMMIT,
                "tree": SOURCE_TREE,
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
                "path": f"agentrunproof-{__version__}-py3-none-any.whl",
                "size": 1,
                "sha256": "0" * 64,
            },
            "environment": environment,
            "certificate": certificate_member,
            "limitations": limitations(),
        },
        directory=directory,
    )
    bundle_path = directory / "bundle.json"
    write_current_bundle(bundle_path, bundle)
    return bundle_path, bundle


def _history_environment() -> dict[str, JsonValue]:
    history = load_history_bundle(HISTORY_BUNDLE)
    environments = history["environments"]
    assert isinstance(environments, list)
    (environment,) = [
        item
        for item in environments
        if isinstance(item, dict) and item.get("environment_id") == ENVIRONMENT_ID
    ]
    return copy.deepcopy(environment)


def _bundle_id(value: dict[str, Any]) -> str:
    unsigned = copy.deepcopy(value)
    unsigned["bundle_id"] = None
    return f"sha256:{sha256_hex(unsigned)}"
