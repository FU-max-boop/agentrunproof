from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from agentrunproof._canonical import JsonValue, sha256_hex
from agentrunproof.cli import main
from agentrunproof.current import comparison as comparison_module
from agentrunproof.current.comparison import (
    COMPARISON_SCHEMA_VERSION,
    MERGED_RECURSIVE_CERTIFICATE_PATH,
    MERGED_TOP_LEVEL_CERTIFICATE_PATH,
    RELEASE_TARGET_ID,
    RELEASED_CERTIFICATE_PATH,
    RUN_SPECS,
    UPSTREAM_COMMIT,
    UPSTREAM_LOCK_SHA256,
    UPSTREAM_PARENT,
    UPSTREAM_PYPROJECT_SHA256,
    UPSTREAM_REPOSITORY,
    UPSTREAM_TARGET_ID,
    UPSTREAM_TRACKED_SOURCE_SHA256,
    UPSTREAM_TREE,
    UPSTREAM_UV_LOCK_SHA256,
    UpstreamComparisonError,
    finalize_upstream_comparison,
    limitations,
    load_upstream_comparison,
    source_wheel_member_path,
    stable_worker_argv,
    write_upstream_comparison,
)
from agentrunproof.history.bundle import finalize_environment, load_history_bundle

ROOT = Path(__file__).resolve().parents[1]
HISTORY_BUNDLE = ROOT / "evidence" / "history" / "v1" / "bundle.json"
SOURCE_COMMIT = "1" * 40
SOURCE_TREE = "2" * 40
HARNESS_WHEEL = "agentrunproof-0.1.2-py3-none-any.whl"
SOURCE_WHEEL_SHA256 = "3" * 64
FIXED_COMMIT = "50d65f65c367a3b09dcd3313ee8d78471c35885e"
FIXED_TREE = "6e5a81072d8ff629cb5eb4413a439a0df0e89b79"
FIXED_PARENT = UPSTREAM_COMMIT
FIXED_TRACKED_SOURCE_SHA256 = "9e28a453501477be5ba09724ce7c2281d72dfc6bb874c03b75d3dc2121b17e92"
FIXED_TARGET_ID = f"openai-agents-python-{FIXED_COMMIT[:8]}"


def test_upstream_comparison_binds_three_ordered_certificates_and_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle_path, bundle = _write_bundle(tmp_path, monkeypatch)

    assert load_upstream_comparison(bundle_path) == bundle
    assert main(["check-upstream-bundle", str(bundle_path)]) == 0
    assert f"VALID {bundle['bundle_id']} upstream-comparison" in capsys.readouterr().out
    assert [run["worker_exit"] for run in bundle["runs"]] == [1, 0, 1]
    assert bundle["targets"][0]["version"] == bundle["targets"][1]["version"] == "0.20.0"
    assert bundle["targets"][0]["wheel"]["sha256"] != bundle["targets"][1]["source_wheel"]["sha256"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda bundle: bundle.update({"unexpected": True}), "fields differ"),
        (lambda bundle: bundle["runs"].reverse(), "run released-pypi-top-level.run_id"),
        (lambda bundle: bundle["runs"][0].update({"worker_exit": 0}), "worker_exit"),
        (
            lambda bundle: bundle["runs"][0]["certificate"].update({"path": "../escape.json"}),
            "path must be",
        ),
        (
            lambda bundle: bundle["targets"][1]["source_wheel"].update(
                {"sha256": bundle["targets"][0]["wheel"]["sha256"]}
            ),
            "direct_url.archive_sha256|different wheel bytes",
        ),
        (
            lambda bundle: bundle["targets"][1].update({"commit": "0" * 40}),
            r"targets\[1\].commit",
        ),
    ],
)
def test_upstream_comparison_rejects_contract_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Any,
    message: str,
) -> None:
    bundle_path, bundle = _write_bundle(tmp_path, monkeypatch)
    forged = copy.deepcopy(bundle)
    mutation(forged)
    _resign(forged)
    bundle_path.write_text(json.dumps(forged, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(UpstreamComparisonError, match=message):
        load_upstream_comparison(bundle_path)


def test_upstream_comparison_rejects_member_replacement_and_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_path, bundle = _write_bundle(tmp_path, monkeypatch)
    certificate = tmp_path / RELEASED_CERTIFICATE_PATH
    certificate.write_bytes(b"replacement\n")
    with pytest.raises(UpstreamComparisonError, match="certificate.size|certificate.sha256"):
        load_upstream_comparison(bundle_path)

    bundle_path, bundle = _write_bundle(tmp_path / "symlink", monkeypatch)
    certificate = bundle_path.with_name(RELEASED_CERTIFICATE_PATH)
    target = bundle_path.with_name("ordinary-file")
    target.write_bytes(certificate.read_bytes())
    certificate.unlink()
    certificate.symlink_to(target.name)
    with pytest.raises(UpstreamComparisonError, match="symbolic link"):
        load_upstream_comparison(bundle_path)

    bundle_path, bundle = _write_bundle(tmp_path / "wheel-symlink", monkeypatch)
    harness = bundle_path.with_name(HARNESS_WHEEL)
    harness.symlink_to(RELEASED_CERTIFICATE_PATH)
    with pytest.raises(UpstreamComparisonError, match="symbolic link"):
        load_upstream_comparison(bundle_path)

    bundle_path, bundle = _write_bundle(tmp_path / "source-wheel-symlink", monkeypatch)
    source_wheel_path = bundle_path.with_name(bundle["targets"][1]["source_wheel"]["path"])
    source_wheel_path.symlink_to(RELEASED_CERTIFICATE_PATH)
    with pytest.raises(UpstreamComparisonError, match="symbolic link"):
        load_upstream_comparison(bundle_path)

    bundle_path, bundle = _write_bundle(tmp_path / "source-wheel-replacement", monkeypatch)
    source_wheel_path = bundle_path.with_name(bundle["targets"][1]["source_wheel"]["path"])
    source_wheel_path.write_bytes(b"not the source-built wheel")
    with pytest.raises(UpstreamComparisonError, match="source-built upstream wheel.size"):
        load_upstream_comparison(bundle_path)


def test_upstream_comparison_rejects_duplicate_keys_and_nonfinite_numbers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_path, _ = _write_bundle(tmp_path, monkeypatch)
    rendered = bundle_path.read_text(encoding="utf-8")
    bundle_path.write_text(
        rendered.replace(
            f'  "schema_version": "{COMPARISON_SCHEMA_VERSION}",',
            f'  "schema_version": "{COMPARISON_SCHEMA_VERSION}",\n'
            f'  "schema_version": "{COMPARISON_SCHEMA_VERSION}",',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(UpstreamComparisonError, match="Duplicate JSON object key"):
        load_upstream_comparison(bundle_path)

    bundle_path, _ = _write_bundle(tmp_path / "nan", monkeypatch)
    rendered = bundle_path.read_text(encoding="utf-8")
    bundle_path.write_text(
        rendered.replace('"worker_timeout_seconds": 120', '"worker_timeout_seconds": NaN'),
        encoding="utf-8",
    )
    with pytest.raises(UpstreamComparisonError, match="Non-finite JSON number"):
        load_upstream_comparison(bundle_path)


def test_exact_fixed_and_recursive_semantic_fingerprints() -> None:
    fixed = _fixed_top_level_certificate()
    comparison_module._validate_fixed_top_level_certificate(fixed)
    fixed["observations"]["streaming"]["phases"][1]["state_transition"][
        "subject_state_unchanged"
    ] = False
    with pytest.raises(UpstreamComparisonError, match="state isolation"):
        comparison_module._validate_fixed_top_level_certificate(fixed)

    recursive = _recursive_failure_certificate()
    comparison_module._validate_recursive_failure_certificate(recursive)
    recursive["invariants"][3]["reason"] = "OTHER_FAILURE"
    with pytest.raises(UpstreamComparisonError, match="invariant fingerprint"):
        comparison_module._validate_recursive_failure_certificate(recursive)

    recursive_success = _recursive_success_certificate()
    comparison_module._validate_recursive_success_certificate(recursive_success)
    recursive_success["observations"]["non_streaming"]["phases"][2]["tool_counts_delta"] = {
        "protected_effect": 0
    }
    with pytest.raises(UpstreamComparisonError, match="approved effects"):
        comparison_module._validate_recursive_success_certificate(recursive_success)

    serialized = _serialized_recursive_certificate("FAIL")
    comparison_module._validate_serialized_recursive_certificate(
        serialized,
        expected_status="FAIL",
    )
    serialized["observations"]["streaming"]["phases"][1]["state_transition"][
        "restored_state_equal"
    ] = False
    with pytest.raises(UpstreamComparisonError, match="restored_state_equal"):
        comparison_module._validate_serialized_recursive_certificate(
            serialized,
            expected_status="FAIL",
        )

    comparison_module._validate_serialized_recursive_certificate(
        _serialized_recursive_certificate("PASS"),
        expected_status="PASS",
    )


def test_upstream_comparison_accepts_the_merged_50d65f65_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_path, bundle = _write_bundle(tmp_path, monkeypatch, include_fixed_target=True)

    assert load_upstream_comparison(bundle_path) == bundle
    assert [target["target_id"] for target in bundle["targets"]] == [
        RELEASE_TARGET_ID,
        UPSTREAM_TARGET_ID,
        FIXED_TARGET_ID,
    ]
    assert [run["worker_exit"] for run in bundle["runs"]] == [1, 0, 1, 0]
    assert (
        bundle["targets"][1]["source_wheel"]["filename"]
        == (bundle["targets"][2]["source_wheel"]["filename"])
    )
    assert (
        bundle["targets"][1]["source_wheel"]["path"]
        != (bundle["targets"][2]["source_wheel"]["path"])
    )


def _write_bundle(
    directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_fixed_target: bool = False,
) -> tuple[Path, dict[str, JsonValue]]:
    directory.mkdir(parents=True, exist_ok=True)
    certificates = {
        RELEASED_CERTIFICATE_PATH: _fake_certificate(
            "a", RELEASE_TARGET_ID, "runstate-sibling-approval-isolation", "FAIL"
        ),
        MERGED_TOP_LEVEL_CERTIFICATE_PATH: _fake_certificate(
            "b", UPSTREAM_TARGET_ID, "runstate-sibling-approval-isolation", "PASS"
        ),
        MERGED_RECURSIVE_CERTIFICATE_PATH: _fake_certificate(
            "c",
            UPSTREAM_TARGET_ID,
            "runstate-recursive-agent-tool-approval-routing",
            "FAIL",
        ),
    }
    if include_fixed_target:
        certificates["fixed-recursive-certificate.json"] = _fake_certificate(
            "d",
            FIXED_TARGET_ID,
            "runstate-recursive-agent-tool-approval-routing",
            "PASS",
        )
    payloads: dict[bytes, dict[str, JsonValue]] = {}
    for filename, certificate in certificates.items():
        data = f"{filename}\n".encode()
        (directory / filename).write_bytes(data)
        payloads[data] = certificate

    def parse(text: str) -> dict[str, JsonValue]:
        return copy.deepcopy(payloads[text.encode()])

    monkeypatch.setattr(comparison_module, "parse_worker_certificate_json", parse)
    monkeypatch.setattr(comparison_module, "validate_current_certificate", lambda value: value)
    monkeypatch.setattr(
        comparison_module, "_validate_fixed_top_level_certificate", lambda value: None
    )
    monkeypatch.setattr(
        comparison_module, "_validate_recursive_failure_certificate", lambda value: None
    )
    monkeypatch.setattr(
        comparison_module, "_validate_recursive_success_certificate", lambda value: None
    )

    release_environment = _release_environment()
    release_wheel = next(
        item for item in release_environment["artifacts"] if item["distribution"] == "openai-agents"
    )
    source_wheel = {
        "distribution": "openai-agents",
        "version": "0.20.0",
        "filename": "openai_agents-0.20.0-py3-none-any.whl",
        "size": 123,
        "sha256": SOURCE_WHEEL_SHA256,
    }
    upstream_environment = _upstream_environment(source_wheel)
    runs: list[dict[str, JsonValue]] = []
    for spec in RUN_SPECS:
        path = directory / spec.certificate_path
        data = path.read_bytes()
        runs.append(
            {
                "run_id": spec.run_id,
                "target_id": spec.target_id,
                "scenario_id": spec.scenario_id,
                "argv": stable_worker_argv(spec.scenario_id),
                "expected_exit": spec.expected_exit,
                "worker_exit": spec.expected_exit,
                "expected_status": spec.expected_status,
                "observed_status": spec.expected_status,
                "certificate": {
                    "path": path.name,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "certificate_id": certificates[path.name]["certificate_id"],
                },
            }
        )
    targets: list[dict[str, JsonValue]] = [
        {
            "target_id": RELEASE_TARGET_ID,
            "kind": "pypi-wheel",
            "distribution": "openai-agents",
            "version": "0.20.0",
            "wheel": release_wheel,
            "environment": release_environment,
        },
        {
            "target_id": UPSTREAM_TARGET_ID,
            "kind": "source-built-wheel",
            "distribution": "openai-agents",
            "version": "0.20.0",
            "repository": UPSTREAM_REPOSITORY,
            "commit": UPSTREAM_COMMIT,
            "tree": UPSTREAM_TREE,
            "parent": UPSTREAM_PARENT,
            "tracked_source_sha256": UPSTREAM_TRACKED_SOURCE_SHA256,
            "pyproject_sha256": UPSTREAM_PYPROJECT_SHA256,
            "uv_lock_sha256": UPSTREAM_UV_LOCK_SHA256,
            "source_wheel": {
                **source_wheel,
                "path": source_wheel_member_path(UPSTREAM_COMMIT),
                "direct_url": {
                    "url_scheme": "file",
                    "url_basename": source_wheel["filename"],
                    "archive_sha256": source_wheel["sha256"],
                },
            },
            "environment": upstream_environment,
        },
    ]
    if include_fixed_target:
        fixed_wheel = {**source_wheel, "sha256": "5" * 64}
        targets.append(
            {
                "target_id": FIXED_TARGET_ID,
                "kind": "source-built-wheel",
                "distribution": "openai-agents",
                "version": "0.20.0",
                "repository": UPSTREAM_REPOSITORY,
                "commit": FIXED_COMMIT,
                "tree": FIXED_TREE,
                "parent": FIXED_PARENT,
                "tracked_source_sha256": FIXED_TRACKED_SOURCE_SHA256,
                "pyproject_sha256": UPSTREAM_PYPROJECT_SHA256,
                "uv_lock_sha256": UPSTREAM_UV_LOCK_SHA256,
                "source_wheel": {
                    **fixed_wheel,
                    "path": source_wheel_member_path(FIXED_COMMIT),
                    "direct_url": {
                        "url_scheme": "file",
                        "url_basename": fixed_wheel["filename"],
                        "archive_sha256": fixed_wheel["sha256"],
                    },
                },
                "environment": _upstream_environment(fixed_wheel),
            }
        )
        path = directory / "fixed-recursive-certificate.json"
        data = path.read_bytes()
        runs.append(
            {
                "run_id": "fixed-source-recursive-two-edge",
                "target_id": FIXED_TARGET_ID,
                "scenario_id": "runstate-recursive-agent-tool-approval-routing",
                "argv": stable_worker_argv("runstate-recursive-agent-tool-approval-routing"),
                "expected_exit": 0,
                "worker_exit": 0,
                "expected_status": "PASS",
                "observed_status": "PASS",
                "certificate": {
                    "path": path.name,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "certificate_id": certificates[path.name]["certificate_id"],
                },
            }
        )
    bundle = finalize_upstream_comparison(
        {
            "schema_version": COMPARISON_SCHEMA_VERSION,
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
            "harness": {"wheel": {"path": HARNESS_WHEEL, "size": 10, "sha256": "4" * 64}},
            "targets": targets,
            "runs": runs,
            "limitations": limitations(),
        },
        directory=directory,
    )
    bundle_path = directory / "bundle.json"
    write_upstream_comparison(bundle_path, bundle)
    return bundle_path, bundle


def _fake_certificate(
    suffix: str,
    target_id: str,
    scenario_id: str,
    status: str,
) -> dict[str, JsonValue]:
    packages = (
        {"openai-agents": "0.20.0", "openai": "2.54.0", "pydantic": "2.13.4"}
        if target_id == RELEASE_TARGET_ID
        else {"openai-agents": "0.20.0", "openai": "3.0.0", "pydantic": "2.12.3"}
    )
    scenario: dict[str, JsonValue] = {"id": scenario_id, "revision": 1}
    if scenario_id != "runstate-sibling-approval-isolation":
        scenario["requested_invariants"] = list(comparison_module._RECURSIVE_INVARIANTS)
    return {
        "certificate_id": f"sha256:{suffix * 64}",
        "overall_status": status,
        "source": {
            "commit": SOURCE_COMMIT,
            "dirty": False,
            "tracked_diff_sha256": sha256_hex(""),
            "untracked_paths_sha256": sha256_hex([]),
            "index_flags_sha256": sha256_hex([]),
        },
        "tool": {"name": "agentrunproof", "version": "0.1.2"},
        "runtime": {
            "python": "3.12.13",
            "implementation": "CPython",
            "platform": {"system": "Linux", "machine": "x86_64"},
            "packages": packages,
        },
        "scenario": scenario,
    }


def _release_environment() -> dict[str, JsonValue]:
    history = load_history_bundle(HISTORY_BUNDLE)
    return copy.deepcopy(
        next(
            item
            for item in history["environments"]
            if item["environment_id"] == comparison_module.RELEASE_ENVIRONMENT_ID
        )
    )


def _upstream_environment(source_wheel: dict[str, JsonValue]) -> dict[str, JsonValue]:
    artifacts: list[dict[str, JsonValue]] = []
    for distribution, (version, digest) in comparison_module._UPSTREAM_DEPENDENCY_ARTIFACTS.items():
        artifacts.append(
            {
                "distribution": distribution,
                "version": version,
                "filename": f"{distribution.replace('-', '_')}-{version}-py3-none-any.whl",
                "size": 1,
                "sha256": digest,
            }
        )
    artifacts.append(copy.deepcopy(source_wheel))
    artifacts.sort(key=lambda item: str(item["distribution"]))
    return finalize_environment(
        {
            "schema_version": "agentrunproof.history-environment/v1",
            "sdk_version": "0.20.0",
            "python": "3.12.13",
            "pip_version": "25.0.1",
            "dependency_metadata_bypassed": False,
            "lock_sha256": UPSTREAM_LOCK_SHA256,
            "artifacts": artifacts,
        }
    )


def _resign(bundle: dict[str, Any]) -> None:
    bundle["bundle_id"] = None
    bundle["bundle_id"] = f"sha256:{sha256_hex(bundle)}"


def _fixed_top_level_certificate() -> dict[str, Any]:
    invariant_names = comparison_module._TOP_LEVEL_INVARIANTS
    phase = {
        "phase_id": "fork-check",
        "interruption_count": 1,
        "tool_counts_delta": {"approval_tool": 0},
        "state_transition": {"subject_state_unchanged": True},
    }
    observation = {
        "final_output": None,
        "interruption_count": 1,
        "tool_counts": {"approval_tool": 0},
        "remaining_model_steps": 1,
        "exception": None,
        "phases": [
            {"phase_id": "initial", "interruption_count": 1},
            phase,
        ],
    }
    return {
        "overall_status": "PASS",
        "scenario": {"requested_invariants": list(invariant_names)},
        "invariants": [
            {"name": name, "status": "PASS", "reason": "OK"} for name in invariant_names
        ],
        "observations": {
            "non_streaming": copy.deepcopy(observation),
            "streaming": copy.deepcopy(observation),
        },
    }


def _recursive_failure_certificate() -> dict[str, Any]:
    invariants = [
        {"name": name, "status": status, "reason": reason}
        for name, status, reason in comparison_module._RECURSIVE_FAILURE_FINGERPRINT
    ]
    digest = "6" * 64
    observation = {
        "final_output": None,
        "interruption_count": 1,
        "tool_counts": {"protected_effect": 0},
        "remaining_model_steps": 1,
        "exception": None,
        "phases": [
            {"phase_id": "initial", "interruption_count": 1},
            {
                "phase_id": "untouched-sibling",
                "interruption_count": 1,
                "tool_counts_delta": {"protected_effect": 0},
                "state_transition": {
                    "subject_state_unchanged": True,
                    "sibling_state_saved": True,
                    "saved_sibling_state_sha256": digest,
                },
            },
            {
                "phase_id": "approved-sibling",
                "interruption_count": 1,
                "tool_counts_delta": {"protected_effect": 0},
                "state_transition": {
                    "saved_sibling_from": "untouched-sibling",
                    "saved_sibling_state_sha256": digest,
                },
            },
        ],
    }
    return {
        "overall_status": "FAIL",
        "scenario": {"requested_invariants": list(comparison_module._RECURSIVE_INVARIANTS)},
        "invariants": invariants,
        "observations": {
            "non_streaming": copy.deepcopy(observation),
            "streaming": copy.deepcopy(observation),
        },
    }


def _recursive_success_certificate() -> dict[str, Any]:
    invariants = [
        {"name": name, "status": "PASS", "reason": "OK"}
        for name in comparison_module._RECURSIVE_INVARIANTS
    ]
    digest = "6" * 64
    observation = {
        "final_output": "outer complete",
        "interruption_count": 0,
        "tool_counts": {"protected_effect": 1},
        "remaining_model_steps": 0,
        "exception": None,
        "phases": [
            {"phase_id": "initial", "interruption_count": 1},
            {
                "phase_id": "untouched-sibling",
                "interruption_count": 1,
                "tool_counts_delta": {"protected_effect": 0},
                "state_transition": {
                    "subject_state_unchanged": True,
                    "sibling_state_saved": True,
                    "saved_sibling_state_sha256": digest,
                },
            },
            {
                "phase_id": "approved-sibling",
                "final_output": "outer complete",
                "interruption_count": 0,
                "tool_counts_delta": {"protected_effect": 1},
                "probes_after": {"protected_effects": ["committed-once"]},
                "state_transition": {
                    "saved_sibling_from": "untouched-sibling",
                    "saved_sibling_state_sha256": digest,
                },
            },
        ],
    }
    return {
        "overall_status": "PASS",
        "scenario": {"requested_invariants": list(comparison_module._RECURSIVE_INVARIANTS)},
        "invariants": invariants,
        "observations": {
            "non_streaming": copy.deepcopy(observation),
            "streaming": copy.deepcopy(observation),
        },
    }


def _serialized_recursive_certificate(status: str) -> dict[str, Any]:
    passed = status == "PASS"
    invariants = (
        [
            {"name": name, "status": "PASS", "reason": "OK"}
            for name in comparison_module._SERIALIZED_RECURSIVE_INVARIANTS
        ]
        if passed
        else [
            {"name": name, "status": result, "reason": reason}
            for name, result, reason in (
                comparison_module._SERIALIZED_RECURSIVE_FAILURE_FINGERPRINT
            )
        ]
    )
    decision = {
        "action": "approve",
        "call_id_sha256": ("18a3037806c47a97bbcc4dda440b96c9213b8ff2114345156c2bde1e44f226ec"),
        "matched": True,
    }
    observation = {
        "final_output": "outer complete" if passed else None,
        "interruption_count": 0 if passed else 1,
        "tool_counts": {"protected_effect": 1 if passed else 0},
        "remaining_model_steps": 0 if passed else 1,
        "exception": None,
        "phases": [
            {"phase_id": "initial", "interruption_count": 1},
            {
                "phase_id": "serialized-approved",
                "interruption_count": 0 if passed else 1,
                "tool_counts_delta": {"protected_effect": 1 if passed else 0},
                "state_transition": {
                    "json_round_trip_requested": True,
                    "json_round_trip_equal": True,
                    "restored_state_equal": True,
                    "state_schema_version": "1.15",
                    "decisions": [decision],
                },
            },
        ],
    }
    return {
        "overall_status": status,
        "scenario": {
            "requested_invariants": list(comparison_module._SERIALIZED_RECURSIVE_INVARIANTS)
        },
        "invariants": invariants,
        "observations": {
            "non_streaming": copy.deepcopy(observation),
            "streaming": copy.deepcopy(observation),
        },
    }
