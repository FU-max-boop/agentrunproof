from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest
from agents.handoffs import (
    get_conversation_history_wrappers,
    set_conversation_history_wrappers,
)

from agentrunproof.builtins import (
    HANDOFF_SESSION_FILTERED_VIEW_PARITY,
    RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_ROUTING,
    RUNSTATE_SIBLING_APPROVAL_ISOLATION,
)
from agentrunproof.certificate import (
    CertificateError,
    build_certificate,
    certificate_json,
    load_certificate,
    schema_path,
    validate_certificate,
    write_certificate,
)
from agentrunproof.engine import run_scenario
from agentrunproof.history.guardrail_atomicity import resumed_guardrail_atomicity_proof
from agentrunproof.history.scenarios import runstate_context_approval_proof
from agentrunproof.scenario import Scenario

_STATE_FORK_TRANSITION_FIELDS = (
    "subject_state_before_sha256",
    "subject_state_after_sha256",
    "subject_state_unchanged",
    "sibling_interruption_call_ids",
    "sibling_decisions",
)
_SAVED_SIBLING_TRANSITION_FIELDS = (
    "sibling_state_saved",
    "saved_sibling_from",
    "saved_sibling_state_sha256",
)


def _recursive_certificate_in_subprocess(path: Path, *, public_payloads: bool) -> dict:
    script = """
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

from agentrunproof.builtins import RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_ROUTING
from agentrunproof.certificate import build_certificate, write_certificate
from agentrunproof.engine import run_scenario

scenario = replace(
    RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_ROUTING,
    public_payloads=sys.argv[2] == "public",
)
certificate = build_certificate(asyncio.run(run_scenario(scenario)))
write_certificate(Path(sys.argv[1]), certificate)
"""
    environment = dict(os.environ)
    source = str(Path.cwd() / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source, environment.get("PYTHONPATH")) if value
    )
    subprocess.run(
        [sys.executable, "-c", script, str(path), "public" if public_payloads else "private"],
        check=True,
        cwd=Path.cwd(),
        env=environment,
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_certificate_is_deterministic_and_matches_json_schema(
    scenario_factory: Callable[..., Scenario],
) -> None:
    proof = await run_scenario(scenario_factory())
    first = build_certificate(proof)
    second = build_certificate(proof)

    assert first == second
    validate_certificate(first)
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(first, schema)


@pytest.mark.asyncio
async def test_readdressing_cannot_hide_handoff_context_filter_drift() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(HANDOFF_SESSION_FILTERED_VIEW_PARITY))
    validate_certificate(certificate)
    forged = copy.deepcopy(certificate)
    for observation in forged["observations"].values():
        observation["phases"][0]["probes_after"]["specialist_context"]["lookup_case"] = 1
    forged["scenario"]["normalized_input_sha256"] = certificate_module._normalized_input_digest(
        scenario_id=forged["scenario"]["id"],
        revision=forged["scenario"]["revision"],
        phase_contracts=forged["scenario"]["phase_contracts"],
        observations=forged["observations"],
    )
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="Invariant results do not match recomputed"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_handoff_certificate_restores_and_excludes_caller_wrappers() -> None:
    original = get_conversation_history_wrappers()
    secret = ("__CALLER_SECRET_START__", "__CALLER_SECRET_END__")
    set_conversation_history_wrappers(start=secret[0], end=secret[1])
    try:
        proof = await run_scenario(HANDOFF_SESSION_FILTERED_VIEW_PARITY)

        assert proof.status == "PASS"
        assert get_conversation_history_wrappers() == secret
        rendered = certificate_json(build_certificate(proof))
        assert secret[0] not in rendered
        assert secret[1] not in rendered
    finally:
        set_conversation_history_wrappers(start=original[0], end=original[1])


@pytest.mark.asyncio
async def test_state_fork_failure_is_a_valid_public_certificate() -> None:
    proof = await run_scenario(RUNSTATE_SIBLING_APPROVAL_ISOLATION)
    certificate = build_certificate(proof)

    assert certificate["overall_status"] == proof.status
    assert "state_fork_isolation" in certificate["scenario"]["requested_invariants"]
    contract = certificate["scenario"]["phase_contracts"]["non_streaming"][1]
    assert len(contract["sibling_decisions"]) == 1
    transition = certificate["observations"]["non_streaming"]["phases"][1]["state_transition"]
    assert set(_STATE_FORK_TRANSITION_FIELDS).issubset(transition)
    validate_certificate(certificate)
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(certificate, schema)


@pytest.mark.asyncio
async def test_state_fork_fields_preserve_private_certificate_redaction() -> None:
    private_scenario = replace(RUNSTATE_SIBLING_APPROVAL_ISOLATION, public_payloads=False)
    certificate = build_certificate(await run_scenario(private_scenario))
    rendered = certificate_json(certificate)

    assert "agentrunproof-sibling-call-1" not in rendered
    assert "approval_tool" not in rendered
    validate_certificate(certificate)
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(certificate, schema)


def test_recursive_approval_branch_is_bound_in_a_valid_public_certificate(
    tmp_path: Path,
) -> None:
    certificate = _recursive_certificate_in_subprocess(
        tmp_path / "recursive-public.json",
        public_payloads=True,
    )
    contracts = certificate["scenario"]["phase_contracts"]["non_streaming"]
    non_streaming_phases = certificate["observations"]["non_streaming"]["phases"]
    fork_transition = non_streaming_phases[1]["state_transition"]
    approved_phase = non_streaming_phases[2]
    approved_transition = approved_phase["state_transition"]
    routing = next(
        result
        for result in certificate["invariants"]
        if result["name"] == "recursive_approval_routing"
    )

    assert contracts[1]["save_sibling_state"] is True
    assert contracts[2]["saved_sibling_from"] == "untouched-sibling"
    assert non_streaming_phases[0]["interruption_count"] == 1
    assert non_streaming_phases[1]["interruption_count"] == 1
    assert non_streaming_phases[1]["tool_counts_delta"] == {"protected_effect": 0}
    assert fork_transition["sibling_state_saved"] is True
    assert approved_transition["saved_sibling_from"] == "untouched-sibling"
    assert (
        approved_transition["saved_sibling_state_sha256"]
        == (fork_transition["saved_sibling_state_sha256"])
    )
    if approved_phase["interruption_count"] == 0:
        assert approved_phase["final_output"] == "outer complete"
        assert approved_phase["tool_counts_delta"] == {"protected_effect": 1}
        assert approved_phase["probes_after"]["protected_effects"] == ["committed-once"]
    else:
        assert approved_phase["interruption_count"] == 1
        assert approved_phase["tool_counts_delta"] == {"protected_effect": 0}
        assert routing["details"]["non_streaming"]["reason"] == (
            "APPROVED_NESTED_STATE_REMAINED_INTERRUPTED"
        )
    validate_certificate(certificate)
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(certificate, schema)


def test_recursive_approval_branch_preserves_private_certificate_redaction(
    tmp_path: Path,
) -> None:
    certificate = _recursive_certificate_in_subprocess(
        tmp_path / "recursive-private.json",
        public_payloads=False,
    )
    rendered = certificate_json(certificate)

    assert "agentrunproof-recursive-protected-call-1" not in rendered
    assert "protected_effect" not in rendered
    assert "untouched-sibling" not in rendered
    validate_certificate(certificate)
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(certificate, schema)


@pytest.mark.asyncio
async def test_certificate_v1_still_accepts_the_original_optional_field_shape() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await runstate_context_approval_proof())
    for contracts in certificate["scenario"]["phase_contracts"].values():
        for contract in contracts:
            contract.pop("sibling_decisions")
            contract.pop("save_sibling_state", None)
            contract.pop("saved_sibling_from", None)
    for observation in certificate["observations"].values():
        for phase in observation["phases"]:
            transition = phase["state_transition"]
            for field in _STATE_FORK_TRANSITION_FIELDS:
                transition.pop(field)
            for field in _SAVED_SIBLING_TRANSITION_FIELDS:
                transition.pop(field, None)
    certificate["scenario"]["normalized_input_sha256"] = (
        certificate_module._normalized_input_digest(
            scenario_id=certificate["scenario"]["id"],
            revision=certificate["scenario"]["revision"],
            phase_contracts=certificate["scenario"]["phase_contracts"],
            observations=certificate["observations"],
        )
    )
    certificate["certificate_id"] = certificate_module._certificate_id(certificate)

    validate_certificate(certificate)
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(certificate, schema)


@pytest.mark.asyncio
async def test_certificate_rejects_payload_tampering(
    scenario_factory: Callable[..., Scenario],
) -> None:
    certificate = build_certificate(await run_scenario(scenario_factory()))
    tampered = copy.deepcopy(certificate)
    tampered["overall_status"] = "FAIL"

    with pytest.raises(CertificateError, match="does not match"):
        validate_certificate(tampered)


@pytest.mark.asyncio
async def test_recomputed_id_cannot_hide_semantic_inconsistency(
    scenario_factory: Callable[..., Scenario],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(scenario_factory()))
    forged = copy.deepcopy(certificate)
    forged["overall_status"] = "FAIL"
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="inconsistent"):
        validate_certificate(forged)

    monkeypatch.setitem(forged, "unexpected", True)
    forged["certificate_id"] = certificate_module._certificate_id(forged)
    with pytest.raises(CertificateError, match="Unexpected top-level"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_runtime_validator_rejects_schema_invalid_scalar_aliases(
    scenario_factory: Callable[..., Scenario],
) -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(scenario_factory()))
    forged_values = []

    forged = copy.deepcopy(certificate)
    forged["tool"]["normalizer_revision"] = True
    forged_values.append(forged)

    forged = copy.deepcopy(certificate)
    forged["scenario"]["revision"] = True
    forged_values.append(forged)

    forged = copy.deepcopy(certificate)
    forged["source"]["dirty"] = 1
    forged_values.append(forged)

    forged = copy.deepcopy(certificate)
    for observation in forged["observations"].values():
        observation["last_agent"] = ""
        observation["phases"][-1]["last_agent"] = ""
    forged_values.append(forged)

    for forged in forged_values:
        forged["certificate_id"] = certificate_module._certificate_id(forged)
        with pytest.raises(CertificateError):
            validate_certificate(forged)


@pytest.mark.asyncio
async def test_recomputed_id_cannot_hide_forged_invariant_result(
    scenario_factory: Callable[..., Scenario],
) -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(scenario_factory()))
    forged = copy.deepcopy(certificate)
    forged["invariants"][0]["details"] = {"variants": 999}
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="recomputed observations"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_recomputed_id_cannot_hide_input_digest_drift(
    scenario_factory: Callable[..., Scenario],
) -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(scenario_factory()))
    forged = copy.deepcopy(certificate)
    forged["scenario"]["normalized_input_sha256"] = "0" * 64
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="does not match observations"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_source_availability_and_digests_must_be_consistent(
    scenario_factory: Callable[..., Scenario],
) -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(scenario_factory()))
    forged = copy.deepcopy(certificate)
    forged["source"] = {
        "commit": None,
        "dirty": None,
        "tracked_diff_sha256": "0" * 64,
        "untracked_paths_sha256": None,
        "index_flags_sha256": None,
    }
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="null commit and null diff digests"):
        validate_certificate(forged)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            {
                "commit": None,
                "dirty": False,
                "tracked_diff_sha256": "12ae32cb1ec02d01eda3581b127c1fee3b0dc53572ed6baf239721a03d82e126",
                "untracked_paths_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
                "index_flags_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            },
            "requires a commit",
        ),
        (
            {
                "commit": "0" * 40,
                "dirty": None,
                "tracked_diff_sha256": None,
                "untracked_paths_sha256": None,
                "index_flags_sha256": None,
            },
            "null commit",
        ),
        (
            {
                "commit": "0" * 40,
                "dirty": False,
                "tracked_diff_sha256": "0" * 64,
                "untracked_paths_sha256": "1" * 64,
                "index_flags_sha256": "2" * 64,
            },
            "canonical empty digests",
        ),
        (
            {
                "commit": "0" * 40,
                "dirty": True,
                "tracked_diff_sha256": "12ae32cb1ec02d01eda3581b127c1fee3b0dc53572ed6baf239721a03d82e126",
                "untracked_paths_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
                "index_flags_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            },
            "cannot carry the canonical empty digests",
        ),
    ],
)
async def test_source_provenance_states_are_fail_closed(
    scenario_factory: Callable[..., Scenario],
    source: dict[str, object],
    message: str,
) -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(scenario_factory()))
    forged = copy.deepcopy(certificate)
    forged["source"] = source
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match=message):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_load_certificate_rejects_duplicate_object_keys(
    scenario_factory: Callable[..., Scenario], tmp_path
) -> None:
    certificate = build_certificate(await run_scenario(scenario_factory()))
    rendered = certificate_json(certificate)
    duplicated = rendered.replace(
        '  "overall_status": "PASS",',
        '  "overall_status": "FAIL",\n  "overall_status": "PASS",',
        1,
    )
    path = tmp_path / "duplicate-key.json"
    path.write_text(duplicated, encoding="utf-8")

    with pytest.raises(CertificateError, match="Duplicate JSON object key"):
        load_certificate(path)


def test_git_command_failure_is_not_reported_as_a_clean_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentrunproof.certificate as certificate_module

    monkeypatch.setattr(certificate_module, "_git", lambda *args, **kwargs: None)

    assert certificate_module._source_provenance() == {
        "commit": None,
        "dirty": None,
        "tracked_diff_sha256": None,
        "untracked_paths_sha256": None,
        "index_flags_sha256": None,
    }


def test_assume_unchanged_index_flag_cannot_hide_a_dirty_tree(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentrunproof.certificate as certificate_module

    package_path = tmp_path / "src" / "agentrunproof"
    package_path.mkdir(parents=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("original\n", encoding="utf-8")
    commands = [
        ["git", "init", "-q"],
        ["git", "add", "tracked.txt"],
        [
            "git",
            "-c",
            "user.name=AgentRunProof",
            "-c",
            "user.email=proof@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        ["git", "update-index", "--assume-unchanged", "tracked.txt"],
    ]
    for command in commands:
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    tracked.write_text("hidden modification\n", encoding="utf-8")
    monkeypatch.setattr(
        certificate_module,
        "__file__",
        str(package_path / "certificate.py"),
    )

    source = certificate_module._source_provenance()

    assert source["dirty"] is True
    assert source["tracked_diff_sha256"] == certificate_module._CLEAN_TRACKED_DIFF_SHA256
    assert source["untracked_paths_sha256"] == certificate_module._CLEAN_UNTRACKED_PATHS_SHA256
    assert source["index_flags_sha256"] != certificate_module._CLEAN_INDEX_FLAGS_SHA256


@pytest.mark.asyncio
async def test_recomputed_id_does_not_make_empty_runtime_identity_valid(
    scenario_factory: Callable[..., Scenario],
) -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(scenario_factory()))
    forged = copy.deepcopy(certificate)
    forged["tool"]["version"] = ""
    forged["runtime"]["python"] = ""
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="tool identity"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_non_public_payloads_are_redacted_before_serialization(
    scenario_factory: Callable[..., Scenario],
) -> None:
    scenario = scenario_factory(
        non_streaming_output="SECRET-FIXTURE-OUTPUT",
        streaming_output="SECRET-FIXTURE-OUTPUT",
        public_payloads=False,
        invariants=("execution_outcome", "exactly_once"),
        expected_tool_counts={"SECRET-TOOL-NAME": 1},
    )
    certificate = build_certificate(await run_scenario(scenario))
    rendered = certificate_json(certificate)

    assert "SECRET-FIXTURE" not in rendered
    assert "SECRET-TOOL-NAME" not in rendered
    assert "fixture agent" not in rendered
    assert "fixture-scenario" not in rendered
    assert "A deterministic test fixture" not in rendered
    assert '"redacted": true' in rendered

    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(certificate, schema)


@pytest.mark.asyncio
async def test_recomputed_id_cannot_forge_a_private_redaction_claim(
    scenario_factory: Callable[..., Scenario],
) -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(scenario_factory(public_payloads=True)))
    forged = copy.deepcopy(certificate)
    forged["redaction"] = {
        "public_payloads": False,
        "policy": "sha256-summary-v1",
    }
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="SHA-256 name digest"):
        validate_certificate(forged)
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(forged, schema)


@pytest.mark.asyncio
async def test_private_certificate_rejects_raw_payload_injection_after_readdressing(
    scenario_factory: Callable[..., Scenario],
) -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(scenario_factory(public_payloads=False)))
    forged = copy.deepcopy(certificate)
    forged["observations"]["non_streaming"]["usage"] = {"secret": "raw-private-value"}
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="usage is not redacted"):
        validate_certificate(forged)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proof_factory", "phase_count"),
    [
        (runstate_context_approval_proof, 2),
        (resumed_guardrail_atomicity_proof, 3),
    ],
)
async def test_multiphase_certificates_round_trip_through_independent_validation(
    proof_factory,
    phase_count: int,
) -> None:
    certificate = build_certificate(await proof_factory())

    validate_certificate(certificate)
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(certificate, schema)
    assert all(
        len(observation["phases"]) == phase_count
        for observation in certificate["observations"].values()
    )


@pytest.mark.asyncio
async def test_readdressing_cannot_hide_a_forged_runstate_transition() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await runstate_context_approval_proof())
    forged = copy.deepcopy(certificate)
    transition = forged["observations"]["non_streaming"]["phases"][1]["state_transition"]
    assert transition["restored_state_equal"] is True
    transition["restored_state_equal"] = False
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="recomputed observations"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_readdressing_both_variants_cannot_hide_restored_state_drift() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await runstate_context_approval_proof())
    forged = copy.deepcopy(certificate)
    for observation in forged["observations"].values():
        transition = observation["phases"][1]["state_transition"]
        assert transition["restored_state_equal"] is True
        transition["restored_state_equal"] = False
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="recomputed observations"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_readdressing_cannot_hide_forged_state_fork_isolation() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(RUNSTATE_SIBLING_APPROVAL_ISOLATION))
    forged = copy.deepcopy(certificate)
    transition = forged["observations"]["non_streaming"]["phases"][1]["state_transition"]
    observed = transition["subject_state_unchanged"]
    assert isinstance(observed, bool)
    transition["subject_state_unchanged"] = not observed
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="recomputed observations"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_readdressing_cannot_rebind_a_saved_approval_branch() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(
        await run_scenario(RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_ROUTING)
    )
    forged = copy.deepcopy(certificate)
    transition = forged["observations"]["non_streaming"]["phases"][2]["state_transition"]
    transition["saved_sibling_state_sha256"] = "0" * 64
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="recomputed observations"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_state_fork_contract_is_bound_to_exact_sibling_decisions() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await run_scenario(RUNSTATE_SIBLING_APPROVAL_ISOLATION))
    forged = copy.deepcopy(certificate)
    contract = forged["scenario"]["phase_contracts"]["non_streaming"][1]
    contract["sibling_decisions"][0]["call_id_sha256"] = "0" * 64
    forged["scenario"]["normalized_input_sha256"] = certificate_module._normalized_input_digest(
        scenario_id=forged["scenario"]["id"],
        revision=forged["scenario"]["revision"],
        phase_contracts=forged["scenario"]["phase_contracts"],
        observations=forged["observations"],
    )
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="recomputed observations"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_readdressing_cannot_hide_aggregate_phase_drift() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await runstate_context_approval_proof())
    forged = copy.deepcopy(certificate)
    for observation in forged["observations"].values():
        observation["usage"] = {"forged": True}
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="Aggregate observation fields"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_readdressing_cannot_hide_private_aggregate_phase_drift() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await runstate_context_approval_proof(public_payloads=False))
    forged = copy.deepcopy(certificate)
    for observation in forged["observations"].values():
        observation["usage"] = {
            "redacted": True,
            "sha256": "0" * 64,
            "bytes": 1,
            "kind": "array",
        }
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="Aggregate observation fields"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_resume_source_ids_are_bound_to_the_referenced_phase() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await runstate_context_approval_proof())
    forged = copy.deepcopy(certificate)
    extra = "f" * 64
    for observation in forged["observations"].values():
        transition = observation["phases"][1]["state_transition"]
        transition["source_interruption_call_ids"].append(extra)
        transition["restored_interruption_call_ids"].append(extra)
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="recomputed observations"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_readdressing_cannot_hide_a_forged_phase_contract() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await runstate_context_approval_proof())
    forged = copy.deepcopy(certificate)
    forged["scenario"]["phase_contracts"]["non_streaming"][1]["expected_tool_counts_delta"][
        "approval_tool"
    ] = 0
    forged["scenario"]["normalized_input_sha256"] = certificate_module._normalized_input_digest(
        scenario_id=forged["scenario"]["id"],
        revision=forged["scenario"]["revision"],
        phase_contracts=forged["scenario"]["phase_contracts"],
        observations=forged["observations"],
    )
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="recomputed observations"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_readdressing_cannot_forge_the_legacy_outcome_of_a_multiphase_plan() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await runstate_context_approval_proof())
    forged = copy.deepcopy(certificate)
    forged["scenario"]["expected_outcome"] = {
        "kind": "interrupted",
        "interruption_count": 1,
        "exception_type": None,
    }
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="multi-phase scenario"):
        validate_certificate(forged)


@pytest.mark.asyncio
async def test_private_multiphase_contract_redacts_probes_and_rejects_raw_injection() -> None:
    import agentrunproof.certificate as certificate_module

    certificate = build_certificate(await runstate_context_approval_proof(public_payloads=False))
    rendered = certificate_json(certificate)
    assert "approval-call-1" not in rendered
    assert '"principal": "ella"' not in rendered
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(certificate, schema)

    forged = copy.deepcopy(certificate)
    contract = forged["scenario"]["phase_contracts"]["non_streaming"][1]
    probe_name = next(iter(contract["expected_probes_after"]))
    contract["expected_probes_after"][probe_name] = {"principal": "raw-secret"}
    forged["scenario"]["normalized_input_sha256"] = certificate_module._normalized_input_digest(
        scenario_id=forged["scenario"]["id"],
        revision=forged["scenario"]["revision"],
        phase_contracts=forged["scenario"]["phase_contracts"],
        observations=forged["observations"],
    )
    forged["certificate_id"] = certificate_module._certificate_id(forged)

    with pytest.raises(CertificateError, match="values are not redacted"):
        validate_certificate(forged)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(forged, schema)


@pytest.mark.asyncio
async def test_certificate_writer_commits_a_private_atomic_file(
    scenario_factory: Callable[..., Scenario], tmp_path
) -> None:
    certificate = build_certificate(await run_scenario(scenario_factory()))
    path = tmp_path / "nested" / "certificate.json"

    write_certificate(path, certificate)

    assert load_certificate(path) == certificate
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(path.parent.glob(f".{path.name}.*"))
