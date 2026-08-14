from __future__ import annotations

import builtins
import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

from ._canonical import (
    CanonicalizationError,
    JsonValue,
    canonical_bytes,
    sha256_hex,
    to_json_value,
)
from ._version import __version__
from .engine import ProofRun
from .invariants import EVALUATOR_REVISION, evaluate_invariants
from .observation import (
    NORMALIZER_REVISION,
    Observation,
    PhaseObservation,
    aggregate_observation,
)
from .scenario import (
    ExpectedOutcome,
    OutcomeKind,
    PhaseContract,
    RunVariant,
    Scenario,
    ScenarioCase,
)

SCHEMA_VERSION = "agentrunproof.certificate/v1"
_CERTIFICATE_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CLEAN_TRACKED_DIFF_SHA256 = sha256_hex("")
_CLEAN_UNTRACKED_PATHS_SHA256 = sha256_hex([])
_CLEAN_INDEX_FLAGS_SHA256 = sha256_hex([])
_TOP_LEVEL_KEYS = {
    "schema_version",
    "certificate_id",
    "overall_status",
    "tool",
    "runtime",
    "source",
    "scenario",
    "observations",
    "invariants",
    "redaction",
    "limitations",
}
_LIMITATIONS = [
    "This certificate is a content-addressed integrity record, not a signature.",
    "It evaluates only the scenario variants and invariants named in this payload.",
    "It does not evaluate model-output quality or remote service behavior.",
    "Private summaries use correlatable, dictionary-guessable hashes; do not publish them.",
    "Dynamic callback identities are descriptive markers only; conformance is decided from recorded observable behavior.",
]
_PRIVATE_SAFE_INVARIANTS = {
    "execution_outcome",
    "stream_parity",
    "tool_linkage",
    "exactly_once",
    "model_script_consumed",
    "state_transitions",
    "state_fork_isolation",
    "phase_contract",
    "session_replay",
}


class CertificateError(ValueError):
    """Raised when a certificate is malformed, inconsistent, or tampered with."""


def build_certificate(proof_run: ProofRun) -> dict[str, JsonValue]:
    scenario = proof_run.scenario
    observations = _sanitize_observation_metadata(
        {
            variant.value: observation.as_json()
            for variant, observation in sorted(
                proof_run.observations.items(), key=lambda item: item[0].value
            )
        },
        public=scenario.public_payloads,
    )
    scenario_id = _public_name(scenario.scenario_id, public=scenario.public_payloads)
    description = _public_name(scenario.description, public=scenario.public_payloads)
    expected_tool_counts = {
        _public_name(name, public=scenario.public_payloads): count
        for name, count in sorted(scenario.expected_tool_counts.items())
    }
    expected_exception_type = scenario.expected_outcome.exception_type
    if expected_exception_type is not None:
        expected_exception_type = _qualified_exception_name(expected_exception_type)
        expected_exception_type = _public_name(
            expected_exception_type, public=scenario.public_payloads
        )
    phase_contracts = _sanitize_phase_contracts(
        proof_run.phase_contracts,
        public=scenario.public_payloads,
    )
    scenario_payload: dict[str, JsonValue] = {
        "id": scenario_id,
        "revision": scenario.revision,
        "description": description,
        "variants": [variant.value for variant in scenario.variants],
        "requested_invariants": [
            name
            if scenario.public_payloads or name in _PRIVATE_SAFE_INVARIANTS
            else _public_name(name, public=False)
            for name in scenario.invariants
        ],
        "expected_tool_counts": to_json_value(expected_tool_counts),
        "expected_outcome": {
            "kind": scenario.expected_outcome.kind.value,
            "interruption_count": scenario.expected_outcome.interruption_count,
            "exception_type": expected_exception_type,
        },
        "phase_contracts": to_json_value(phase_contracts),
        "normalized_input_sha256": _normalized_input_digest(
            scenario_id=scenario_id,
            revision=scenario.revision,
            phase_contracts=phase_contracts,
            observations=observations,
        ),
    }
    redaction_payload: dict[str, JsonValue] = {
        "public_payloads": scenario.public_payloads,
        "policy": ("public-synthetic-v1" if scenario.public_payloads else "sha256-summary-v1"),
    }
    decoded_contracts = _validate_phase_contracts(
        phase_contracts, {variant.value for variant in scenario.variants}
    )
    decoded_observations = _validate_observations(
        observations,
        {variant.value for variant in scenario.variants},
        public_payloads=scenario.public_payloads,
    )
    invariant_payload = _recomputed_invariant_results(
        scenario_value=cast(dict[str, Any], scenario_payload),
        redaction_value=redaction_payload,
        observations=decoded_observations,
        phase_contracts=decoded_contracts,
    )
    statuses = {cast(str, result["status"]) for result in invariant_payload}
    recomputed_overall = (
        "FAIL" if "FAIL" in statuses else "PASS" if statuses and statuses == {"PASS"} else "NOT_RUN"
    )
    if recomputed_overall != proof_run.status:
        raise CertificateError("Redacted evidence changed the proof result.")
    payload: dict[str, JsonValue] = {
        "schema_version": SCHEMA_VERSION,
        "certificate_id": None,
        "overall_status": proof_run.status,
        "tool": {
            "name": "agentrunproof",
            "version": __version__,
            "normalizer_revision": NORMALIZER_REVISION,
            "evaluator_revision": EVALUATOR_REVISION,
        },
        "runtime": _runtime_metadata(),
        "source": _source_provenance(),
        "scenario": scenario_payload,
        "observations": to_json_value(observations),
        "invariants": to_json_value(invariant_payload),
        "redaction": redaction_payload,
        "limitations": list(_LIMITATIONS),
    }
    payload["certificate_id"] = _certificate_id(payload)
    validate_certificate(payload)
    return payload


def validate_certificate(certificate: Any) -> dict[str, JsonValue]:
    if not isinstance(certificate, dict):
        raise CertificateError("The certificate must be a JSON object.")
    if set(certificate) != _TOP_LEVEL_KEYS:
        missing = sorted(_TOP_LEVEL_KEYS - set(certificate))
        extra = sorted(set(certificate) - _TOP_LEVEL_KEYS)
        raise CertificateError(f"Unexpected top-level fields; missing={missing}, extra={extra}.")
    if certificate.get("schema_version") != SCHEMA_VERSION:
        raise CertificateError("Unsupported certificate schema version.")
    certificate_id = certificate.get("certificate_id")
    if not isinstance(certificate_id, str) or not _CERTIFICATE_PATTERN.fullmatch(certificate_id):
        raise CertificateError("certificate_id must be a lowercase SHA-256 identifier.")
    if certificate_id != _certificate_id(certificate):
        raise CertificateError("certificate_id does not match the canonical payload.")

    overall = certificate.get("overall_status")
    if overall not in {"PASS", "FAIL", "NOT_RUN"}:
        raise CertificateError("Invalid overall_status.")
    _validate_tool(certificate.get("tool"))
    _validate_runtime(certificate.get("runtime"))
    _validate_source(certificate.get("source"))
    scenario_value = _require_mapping(certificate.get("scenario"), "scenario")
    variants, phase_contracts = _validate_scenario(scenario_value)
    redaction_value = certificate.get("redaction")
    encoded_observations = _require_mapping(certificate.get("observations"), "observations")
    _validate_redaction(
        redaction_value,
        scenario=scenario_value,
        observations=encoded_observations,
    )
    redaction = _require_mapping(redaction_value, "redaction")
    observations = _validate_observations(
        encoded_observations,
        variants,
        public_payloads=cast(bool, redaction["public_payloads"]),
    )
    invariant_names, statuses = _validate_invariants(certificate.get("invariants"))
    requested = cast(list[str], scenario_value["requested_invariants"])
    if invariant_names != requested:
        raise CertificateError("Invariant results must match requested_invariants in order.")
    _validate_normalized_input_digest(scenario_value, certificate.get("observations"))
    _validate_recomputed_invariants(
        scenario_value=scenario_value,
        redaction_value=redaction_value,
        observations=observations,
        phase_contracts=phase_contracts,
        encoded_results=certificate.get("invariants"),
    )
    expected_overall = (
        "FAIL" if "FAIL" in statuses else "PASS" if statuses and statuses == {"PASS"} else "NOT_RUN"
    )
    if overall != expected_overall:
        raise CertificateError("overall_status is inconsistent with invariant results.")
    if certificate.get("limitations") != _LIMITATIONS:
        raise CertificateError("limitations do not match the certificate v1 contract.")
    return cast(dict[str, JsonValue], copy.deepcopy(certificate))


def load_certificate(path: Path) -> dict[str, JsonValue]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_non_finite_json,
            object_pairs_hook=_unique_object_pairs,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise CertificateError(f"Cannot read certificate: {error}") from error
    return validate_certificate(payload)


def certificate_json(certificate: dict[str, JsonValue]) -> str:
    validate_certificate(certificate)
    return (
        json.dumps(certificate, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def _certificate_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise CertificateError("The certificate payload must be an object.")
    unsigned = copy.deepcopy(payload)
    unsigned["certificate_id"] = None
    try:
        return f"sha256:{sha256_hex(unsigned)}"
    except CanonicalizationError as error:
        raise CertificateError(f"Certificate is not canonical JSON: {error}") from error


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is forbidden: {value}.")


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key is forbidden: {key!r}.")
        result[key] = value
    return result


def _runtime_metadata() -> dict[str, JsonValue]:
    packages: dict[str, JsonValue] = {}
    for distribution in ("openai-agents", "openai", "pydantic"):
        try:
            packages[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            packages[distribution] = None
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "packages": packages,
    }


def _source_provenance() -> dict[str, JsonValue]:
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return _unavailable_source_provenance()
    commit = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    tracked_diff = _git(root, "diff", "--binary", "HEAD", "--") if commit else None
    index_listing = _git(root, "ls-files", "-v") if commit else None
    if not commit or status is None or tracked_diff is None or index_listing is None:
        return _unavailable_source_provenance()
    untracked = sorted(
        line[3:] for line in status.splitlines() if line.startswith("?? ") and len(line) > 3
    )
    hidden_index_flags = sorted(
        line
        for line in index_listing.splitlines()
        if line and (line[0].islower() or line[0] == "S")
    )
    return {
        "commit": commit or None,
        "dirty": bool(status) or bool(hidden_index_flags),
        "tracked_diff_sha256": sha256_hex(tracked_diff),
        "untracked_paths_sha256": sha256_hex(untracked),
        "index_flags_sha256": sha256_hex(hidden_index_flags),
    }


def _unavailable_source_provenance() -> dict[str, JsonValue]:
    return {
        "commit": None,
        "dirty": None,
        "tracked_diff_sha256": None,
        "untracked_paths_sha256": None,
        "index_flags_sha256": None,
    }


def _git(root: Path, *arguments: str) -> str | None:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _validate_tool(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "name",
        "version",
        "normalizer_revision",
        "evaluator_revision",
    }:
        raise CertificateError("tool contains an invalid protocol identity.")
    if (
        value.get("name") != "agentrunproof"
        or not isinstance(value.get("version"), str)
        or not value.get("version")
        or not isinstance(value.get("normalizer_revision"), int)
        or isinstance(value.get("normalizer_revision"), bool)
        or value.get("normalizer_revision") != NORMALIZER_REVISION
        or not isinstance(value.get("evaluator_revision"), int)
        or isinstance(value.get("evaluator_revision"), bool)
        or value.get("evaluator_revision") != EVALUATOR_REVISION
    ):
        raise CertificateError("Invalid tool identity.")


def _validate_runtime(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "python",
        "implementation",
        "platform",
        "packages",
    }:
        raise CertificateError("Invalid runtime metadata.")
    if (
        not isinstance(value.get("python"), str)
        or not value.get("python")
        or not isinstance(value.get("implementation"), str)
        or not value.get("implementation")
    ):
        raise CertificateError("Invalid Python runtime metadata.")
    platform_value = value.get("platform")
    packages = value.get("packages")
    if not isinstance(platform_value, dict) or set(platform_value) != {"system", "machine"}:
        raise CertificateError("Invalid platform metadata.")
    if not all(isinstance(platform_value.get(key), str) for key in ("system", "machine")):
        raise CertificateError("Invalid platform values.")
    if not isinstance(packages, dict) or set(packages) != {
        "openai-agents",
        "openai",
        "pydantic",
    }:
        raise CertificateError("Invalid package metadata.")
    if not all(
        value is None or isinstance(value, str) and bool(value) for value in packages.values()
    ):
        raise CertificateError("Invalid package version values.")


def _validate_source(value: Any) -> None:
    expected = {
        "commit",
        "dirty",
        "tracked_diff_sha256",
        "untracked_paths_sha256",
        "index_flags_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise CertificateError("Invalid source provenance.")
    dirty = value.get("dirty")
    if dirty is not None and not isinstance(dirty, bool):
        raise CertificateError("source.dirty must be boolean or null.")
    commit = value.get("commit")
    if commit is not None and (
        not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commit)
    ):
        raise CertificateError("Invalid source commit.")
    for key in ("tracked_diff_sha256", "untracked_paths_sha256", "index_flags_sha256"):
        digest = value.get(key)
        if digest is not None and (
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise CertificateError(f"Invalid {key}.")
    tracked_digest = value.get("tracked_diff_sha256")
    untracked_digest = value.get("untracked_paths_sha256")
    index_flags_digest = value.get("index_flags_sha256")
    digests = [tracked_digest, untracked_digest, index_flags_digest]
    if dirty is None:
        if commit is not None or any(digest is not None for digest in digests):
            raise CertificateError(
                "Unavailable source state must have a null commit and null diff digests."
            )
        return
    if commit is None or any(digest is None for digest in digests):
        raise CertificateError("Available source state requires a commit and both diff digests.")
    is_clean_digest_pair = (
        tracked_digest == _CLEAN_TRACKED_DIFF_SHA256
        and untracked_digest == _CLEAN_UNTRACKED_PATHS_SHA256
        and index_flags_digest == _CLEAN_INDEX_FLAGS_SHA256
    )
    if dirty is False and not is_clean_digest_pair:
        raise CertificateError("A clean source state must carry the canonical empty digests.")
    if dirty is True and is_clean_digest_pair:
        raise CertificateError("A dirty source state cannot carry the canonical empty digests.")


def _validate_scenario(
    value: dict[str, Any],
) -> tuple[set[str], dict[RunVariant, tuple[PhaseContract, ...]]]:
    expected = {
        "id",
        "revision",
        "description",
        "variants",
        "requested_invariants",
        "expected_tool_counts",
        "expected_outcome",
        "phase_contracts",
        "normalized_input_sha256",
    }
    if set(value) != expected:
        raise CertificateError("Invalid scenario metadata.")
    if (
        not isinstance(value.get("id"), str)
        or not value.get("id")
        or any(character.isspace() for character in value["id"])
    ):
        raise CertificateError("Invalid scenario id.")
    if not isinstance(value.get("description"), str) or not value.get("description"):
        raise CertificateError("Invalid scenario description.")
    if (
        not isinstance(value.get("revision"), int)
        or isinstance(value.get("revision"), bool)
        or value["revision"] < 1
    ):
        raise CertificateError("Invalid scenario revision.")
    variants = value.get("variants")
    if (
        not isinstance(variants, list)
        or not variants
        or not all(isinstance(item, str) and item for item in variants)
    ):
        raise CertificateError("Invalid scenario variants.")
    if len(set(variants)) != len(variants):
        raise CertificateError("Scenario variants must be unique.")
    requested = value.get("requested_invariants")
    if (
        not isinstance(requested, list)
        or not requested
        or not all(isinstance(item, str) and item for item in requested)
    ):
        raise CertificateError("Invalid requested invariants.")
    if len(set(requested)) != len(requested):
        raise CertificateError("Requested invariants must be unique.")
    expected_tool_counts = value.get("expected_tool_counts")
    if not isinstance(expected_tool_counts, dict) or not all(
        isinstance(name, str)
        and bool(name)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
        for name, count in expected_tool_counts.items()
    ):
        raise CertificateError("Invalid expected tool counts.")
    _validate_expected_outcome(value.get("expected_outcome"))
    digest = value.get("normalized_input_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise CertificateError("Invalid normalized input digest.")
    variant_set = set(variants)
    contracts = _validate_phase_contracts(value.get("phase_contracts"), variant_set)
    _validate_scenario_outcome_binding(value.get("expected_outcome"), contracts)
    return variant_set, contracts


def _qualified_exception_name(value: str) -> str:
    if "." in value:
        return value
    candidate = getattr(builtins, value, None)
    if isinstance(candidate, type) and issubclass(candidate, BaseException):
        return f"{candidate.__module__}.{candidate.__qualname__}"
    return value


def _validate_scenario_outcome_binding(
    value: Any,
    contracts: dict[RunVariant, tuple[PhaseContract, ...]],
) -> None:
    encoded = _decode_expected_outcome(value, "expected_outcome")
    single_phase = all(len(phases) == 1 for phases in contracts.values())
    if single_phase:
        phase_outcomes = {phases[0].expected_outcome for phases in contracts.values()}
        if len(phase_outcomes) != 1 or encoded not in phase_outcomes:
            raise CertificateError(
                "A single-phase scenario outcome must match every phase contract."
            )
        return
    if encoded != ExpectedOutcome():
        raise CertificateError(
            "A multi-phase scenario must keep the legacy scenario outcome at completed."
        )


def _sanitize_phase_contracts(
    contracts: dict[RunVariant, tuple[PhaseContract, ...]], *, public: bool
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for variant, phases in sorted(contracts.items(), key=lambda item: item[0].value):
        phase_names = {
            phase.phase_id: _public_name(phase.phase_id, public=public) for phase in phases
        }
        encoded: list[JsonValue] = []
        for phase in phases:
            expected_exception = phase.expected_outcome.exception_type
            if expected_exception is not None:
                expected_exception = _public_name(expected_exception, public=public)
            callbacks = cast(dict[str, Any], phase.callback_markers)
            before = callbacks.get("before")
            probe_callbacks = callbacks.get("probes")
            encoded.append(
                to_json_value(
                    {
                        "phase_id": phase_names[phase.phase_id],
                        "input_kind": phase.input_kind,
                        "source_phase": (
                            phase_names.get(phase.source_phase)
                            if phase.source_phase is not None
                            else None
                        ),
                        "json_round_trip": phase.json_round_trip,
                        "decisions": list(phase.decisions),
                        "sibling_decisions": list(phase.sibling_decisions),
                        "expected_outcome": {
                            "kind": phase.expected_outcome.kind.value,
                            "interruption_count": phase.expected_outcome.interruption_count,
                            "exception_type": expected_exception,
                        },
                        "expected_tool_counts_delta": {
                            _public_name(name, public=public): count
                            for name, count in sorted(phase.expected_tool_counts_delta.items())
                        },
                        "expected_probes_after": {
                            _public_name(name, public=public): value
                            for name, value in sorted(phase.expected_probes_after.items())
                        },
                        "callback_markers": {
                            "before": (
                                _public_name(before, public=public)
                                if isinstance(before, str)
                                else None
                            ),
                            "probes": {
                                _public_name(name, public=public): _public_name(
                                    marker, public=public
                                )
                                for name, marker in sorted(
                                    cast(dict[str, str], probe_callbacks).items()
                                )
                            }
                            if isinstance(probe_callbacks, dict)
                            else {},
                        },
                        "model_group": _public_name(phase.model_group, public=public),
                    }
                )
            )
        result[variant.value] = encoded
    return result


def _validate_phase_contracts(
    value: Any, variants: set[str]
) -> dict[RunVariant, tuple[PhaseContract, ...]]:
    if not isinstance(value, dict) or set(value) != variants:
        raise CertificateError("phase_contracts must match the declared variants exactly.")
    decoded: dict[RunVariant, tuple[PhaseContract, ...]] = {}
    required = {
        "phase_id",
        "input_kind",
        "source_phase",
        "json_round_trip",
        "decisions",
        "expected_outcome",
        "expected_tool_counts_delta",
        "expected_probes_after",
        "callback_markers",
        "model_group",
    }
    optional = {"sibling_decisions"}
    for variant_name, raw_phases in value.items():
        if not isinstance(raw_phases, list) or not raw_phases:
            raise CertificateError(f"Invalid phase contracts for {variant_name}.")
        phases: list[PhaseContract] = []
        seen: set[str] = set()
        for index, raw_phase in enumerate(raw_phases):
            label = f"phase_contracts.{variant_name}[{index}]"
            if (
                not isinstance(raw_phase, dict)
                or not required.issubset(raw_phase)
                or not set(raw_phase).issubset(required | optional)
            ):
                raise CertificateError(f"Invalid {label} fields.")
            phase_id = raw_phase.get("phase_id")
            model_group = raw_phase.get("model_group")
            if (
                not isinstance(phase_id, str)
                or not phase_id
                or any(character.isspace() for character in phase_id)
                or phase_id in seen
            ):
                raise CertificateError(f"Invalid or duplicate {label}.phase_id.")
            if (
                not isinstance(model_group, str)
                or not model_group
                or any(character.isspace() for character in model_group)
            ):
                raise CertificateError(f"Invalid {label}.model_group.")
            input_kind = raw_phase.get("input_kind")
            source_phase = raw_phase.get("source_phase")
            round_trip = raw_phase.get("json_round_trip")
            if input_kind not in {"literal", "resume"} or not isinstance(round_trip, bool):
                raise CertificateError(f"Invalid {label} input contract.")
            if input_kind == "literal":
                if source_phase is not None or round_trip:
                    raise CertificateError(f"Literal {label} cannot declare a resume transition.")
            elif not isinstance(source_phase, str) or source_phase not in seen:
                raise CertificateError(f"Resume {label} must reference an earlier phase.")
            decisions = _validate_contract_decisions(raw_phase.get("decisions"), label)
            if input_kind == "literal" and decisions:
                raise CertificateError(f"Literal {label} cannot declare decisions.")
            sibling_decisions = _validate_contract_decisions(
                raw_phase.get("sibling_decisions", []),
                label,
                field="sibling_decisions",
            )
            if sibling_decisions and (input_kind != "resume" or round_trip or decisions):
                raise CertificateError(
                    f"{label}.sibling_decisions require a direct resume with no subject decisions."
                )
            expected_outcome = _decode_expected_outcome(
                raw_phase.get("expected_outcome"), f"{label}.expected_outcome"
            )
            expected_delta = _validate_counts_mapping(
                raw_phase.get("expected_tool_counts_delta"),
                f"{label}.expected_tool_counts_delta",
            )
            probes = raw_phase.get("expected_probes_after")
            if not isinstance(probes, dict) or not all(
                isinstance(name, str) and bool(name) for name in probes
            ):
                raise CertificateError(f"Invalid {label}.expected_probes_after.")
            callbacks = raw_phase.get("callback_markers")
            if not isinstance(callbacks, dict) or set(callbacks) != {"before", "probes"}:
                raise CertificateError(f"Invalid {label}.callback_markers.")
            before = callbacks.get("before")
            callback_probes = callbacks.get("probes")
            if before is not None and (not isinstance(before, str) or not before):
                raise CertificateError(f"Invalid {label} before callback marker.")
            if not isinstance(callback_probes, dict) or set(callback_probes) != set(probes):
                raise CertificateError(f"Invalid {label} probe callback markers.")
            if not all(
                isinstance(name, str) and bool(name) and isinstance(marker, str) and bool(marker)
                for name, marker in callback_probes.items()
            ):
                raise CertificateError(f"Invalid {label} probe callback identity.")
            phases.append(
                PhaseContract(
                    phase_id=phase_id,
                    input_kind=cast(str, input_kind),
                    source_phase=source_phase,
                    json_round_trip=round_trip,
                    decisions=tuple(decisions),
                    expected_outcome=expected_outcome,
                    expected_tool_counts_delta=expected_delta,
                    expected_probes_after=cast(dict[str, Any], probes),
                    callback_markers=cast(dict[str, Any], callbacks),
                    model_group=model_group,
                    sibling_decisions=tuple(sibling_decisions),
                )
            )
            seen.add(phase_id)
        try:
            variant = RunVariant(variant_name)
        except ValueError as error:
            raise CertificateError(f"Unsupported phase contract variant {variant_name}.") from error
        decoded[variant] = tuple(phases)
    return decoded


def _validate_contract_decisions(
    value: Any,
    label: str,
    *,
    field: str = "decisions",
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise CertificateError(f"Invalid {label}.{field}.")
    result: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for decision in value:
        if not isinstance(decision, dict) or set(decision) != {
            "action",
            "call_id_sha256",
            "rejection_message",
        }:
            raise CertificateError(f"Invalid {label}.{field} fields.")
        digest = decision.get("call_id_sha256")
        if (
            decision.get("action") not in {"approve", "reject"}
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or digest in identifiers
        ):
            raise CertificateError(f"Invalid or duplicate {label}.{field} entry.")
        identifiers.add(digest)
        result.append(cast(dict[str, Any], copy.deepcopy(decision)))
    return result


def _validate_counts_mapping(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict) or not all(
        isinstance(name, str)
        and bool(name)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
        for name, count in value.items()
    ):
        raise CertificateError(f"Invalid {label}.")
    return cast(dict[str, int], copy.deepcopy(value))


def _validate_observations(
    value: Any,
    variants: set[str],
    *,
    public_payloads: bool,
) -> dict[RunVariant, Observation]:
    if not isinstance(value, dict) or set(value) != variants:
        raise CertificateError("Observations must match the declared variants exactly.")
    required = {
        "variant",
        "status",
        "final_output",
        "last_agent",
        "new_items",
        "model_calls",
        "session_items",
        "session_operations",
        "tool_counts",
        "tool_linkage",
        "interruption_call_ids",
        "interruption_count",
        "usage",
        "guardrails",
        "stream_event_types",
        "remaining_model_steps",
        "exception",
        "phases",
    }
    decoded: dict[RunVariant, Observation] = {}
    for variant, observation in value.items():
        if not isinstance(observation, dict) or set(observation) != required:
            raise CertificateError(f"Invalid observation for {variant}.")
        if observation.get("variant") != variant:
            raise CertificateError(f"Observation variant mismatch for {variant}.")
        if observation.get("status") not in {"PASS", "ERROR"}:
            raise CertificateError(f"Invalid observation status for {variant}.")
        if not isinstance(observation.get("remaining_model_steps"), int) or isinstance(
            observation.get("remaining_model_steps"), bool
        ):
            raise CertificateError(f"Invalid model-step count for {variant}.")
        if observation["remaining_model_steps"] < 0:
            raise CertificateError(f"Negative model-step count for {variant}.")
        _validate_observation_fields(variant, observation)
        raw_phases = observation.get("phases")
        if not isinstance(raw_phases, list) or not raw_phases:
            raise CertificateError(f"Invalid phase observations for {variant}.")
        phases = tuple(
            _decode_phase_observation(raw_phase, variant=variant, index=index)
            for index, raw_phase in enumerate(raw_phases)
        )
        if len({phase.phase_id for phase in phases}) != len(phases):
            raise CertificateError(f"Duplicate phase observation id for {variant}.")
        try:
            run_variant = RunVariant(variant)
        except ValueError as error:
            raise CertificateError(f"Unsupported observation variant {variant}.") from error
        decoded_observation = Observation(
            variant=run_variant,
            status=cast(str, observation["status"]),
            final_output=observation["final_output"],
            last_agent=cast(str | None, observation["last_agent"]),
            new_items=cast(list[JsonValue], observation["new_items"]),
            model_calls=cast(list[JsonValue], observation["model_calls"]),
            session_items=cast(list[JsonValue] | None, observation["session_items"]),
            session_operations=cast(list[JsonValue], observation["session_operations"]),
            tool_counts=cast(dict[str, int], observation["tool_counts"]),
            tool_linkage=cast(dict[str, JsonValue], observation["tool_linkage"]),
            interruption_call_ids=cast(list[str], observation["interruption_call_ids"]),
            interruption_count=cast(int, observation["interruption_count"]),
            usage=observation["usage"],
            guardrails=observation["guardrails"],
            stream_event_types=cast(list[str], observation["stream_event_types"]),
            remaining_model_steps=cast(int, observation["remaining_model_steps"]),
            exception=cast(dict[str, JsonValue] | None, observation["exception"]),
            phases=phases,
        )
        expected_aggregate = _aggregate_encoded_observation(
            variant=run_variant,
            phases=phases,
            public_payloads=public_payloads,
        )
        if decoded_observation.as_json() != expected_aggregate:
            raise CertificateError(
                f"Aggregate observation fields do not match phases for {variant}."
            )
        decoded[run_variant] = decoded_observation
    return decoded


def _aggregate_encoded_observation(
    *,
    variant: RunVariant,
    phases: tuple[PhaseObservation, ...],
    public_payloads: bool,
) -> dict[str, JsonValue]:
    aggregate = aggregate_observation(variant=variant, phases=list(phases)).as_json()
    if not public_payloads and len(phases) > 1:
        aggregate["usage"] = _redacted_summary(aggregate["usage"])
        aggregate["guardrails"] = _redacted_summary(aggregate["guardrails"])
    return aggregate


def _decode_phase_observation(value: Any, *, variant: str, index: int) -> PhaseObservation:
    label = f"observations.{variant}.phases[{index}]"
    required = {
        "phase_id",
        "model_group",
        "input_kind",
        "status",
        "final_output",
        "last_agent",
        "new_items",
        "model_calls",
        "session_items_before",
        "session_items_after",
        "session_operations",
        "tool_counts_delta",
        "tool_linkage",
        "interruption_call_ids",
        "interruption_count",
        "usage",
        "guardrails",
        "stream_event_types",
        "remaining_model_steps",
        "exception",
        "state_transition",
        "probes_before",
        "probes_after",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise CertificateError(f"Invalid {label} fields.")
    phase_id = value.get("phase_id")
    model_group = value.get("model_group")
    input_kind = value.get("input_kind")
    if (
        not isinstance(phase_id, str)
        or not phase_id
        or any(character.isspace() for character in phase_id)
    ):
        raise CertificateError(f"Invalid {label}.phase_id.")
    if (
        not isinstance(model_group, str)
        or not model_group
        or any(character.isspace() for character in model_group)
    ):
        raise CertificateError(f"Invalid {label}.model_group.")
    if input_kind not in {"literal", "resume"}:
        raise CertificateError(f"Invalid {label}.input_kind.")
    if value.get("status") not in {"PASS", "ERROR"}:
        raise CertificateError(f"Invalid {label}.status.")
    last_agent = value.get("last_agent")
    if last_agent is not None and (not isinstance(last_agent, str) or not last_agent):
        raise CertificateError(f"Invalid {label}.last_agent.")
    for key in ("new_items", "model_calls", "session_operations", "stream_event_types"):
        if not isinstance(value.get(key), list):
            raise CertificateError(f"Invalid {label}.{key}.")
    for key in ("session_items_before", "session_items_after"):
        items = value.get(key)
        if items is not None and not isinstance(items, list):
            raise CertificateError(f"Invalid {label}.{key}.")
    event_types = cast(list[Any], value["stream_event_types"])
    if not all(isinstance(item, str) and item for item in event_types):
        raise CertificateError(f"Invalid {label}.stream_event_types.")
    tool_counts_delta = _validate_counts_mapping(
        value.get("tool_counts_delta"), f"{label}.tool_counts_delta"
    )
    _validate_phase_tool_linkage(value.get("tool_linkage"), label)
    interruption_call_ids = _validate_identifier_list(
        value.get("interruption_call_ids"), f"{label}.interruption_call_ids"
    )
    interruption_count = value.get("interruption_count")
    remaining_model_steps = value.get("remaining_model_steps")
    if (
        not isinstance(interruption_count, int)
        or isinstance(interruption_count, bool)
        or interruption_count < 0
    ):
        raise CertificateError(f"Invalid {label}.interruption_count.")
    if (
        not isinstance(remaining_model_steps, int)
        or isinstance(remaining_model_steps, bool)
        or remaining_model_steps < 0
    ):
        raise CertificateError(f"Invalid {label}.remaining_model_steps.")
    exception = _validate_exception(value.get("exception"), label)
    expected_status = "ERROR" if exception is not None else "PASS"
    if value.get("status") != expected_status:
        raise CertificateError(f"{label} status/exception mismatch.")
    state_transition = _validate_state_transition(
        value.get("state_transition"), f"{label}.state_transition"
    )
    probes_before = _validate_probe_mapping(value.get("probes_before"), f"{label}.probes_before")
    probes_after = _validate_probe_mapping(value.get("probes_after"), f"{label}.probes_after")
    return PhaseObservation(
        phase_id=phase_id,
        model_group=model_group,
        input_kind=cast(str, input_kind),
        status=cast(str, value["status"]),
        final_output=cast(JsonValue, value["final_output"]),
        last_agent=last_agent,
        new_items=cast(list[JsonValue], value["new_items"]),
        model_calls=cast(list[JsonValue], value["model_calls"]),
        session_items_before=cast(list[JsonValue] | None, value["session_items_before"]),
        session_items_after=cast(list[JsonValue] | None, value["session_items_after"]),
        session_operations=cast(list[JsonValue], value["session_operations"]),
        tool_counts_delta=tool_counts_delta,
        tool_linkage=cast(dict[str, JsonValue], value["tool_linkage"]),
        interruption_call_ids=interruption_call_ids,
        interruption_count=interruption_count,
        usage=cast(JsonValue, value["usage"]),
        guardrails=cast(JsonValue, value["guardrails"]),
        stream_event_types=cast(list[str], event_types),
        remaining_model_steps=remaining_model_steps,
        exception=exception,
        state_transition=state_transition,
        probes_before=probes_before,
        probes_after=probes_after,
    )


def _validate_phase_tool_linkage(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {
        "generated",
        "session_before",
        "session_after",
        "model_inputs",
    }:
        raise CertificateError(f"Invalid {label}.tool_linkage.")
    generated = value.get("generated")
    session_before = value.get("session_before")
    session_after = value.get("session_after")
    model_inputs = value.get("model_inputs")
    if not isinstance(generated, list):
        raise CertificateError(f"Invalid {label}.tool_linkage.generated.")
    if session_before is not None and not isinstance(session_before, list):
        raise CertificateError(f"Invalid {label}.tool_linkage.session_before.")
    if session_after is not None and not isinstance(session_after, list):
        raise CertificateError(f"Invalid {label}.tool_linkage.session_after.")
    if not isinstance(model_inputs, list) or not all(
        isinstance(events, list) for events in model_inputs
    ):
        raise CertificateError(f"Invalid {label}.tool_linkage.model_inputs.")
    channels = [generated]
    if isinstance(session_before, list):
        channels.append(session_before)
    if isinstance(session_after, list):
        channels.append(session_after)
    channels.extend(cast(list[list[Any]], model_inputs))
    _validate_linkage_events(channels, label)


def _validate_linkage_events(channels: list[list[Any]], label: str) -> None:
    for event in [item for channel in channels for item in channel]:
        if not isinstance(event, dict) or set(event) != {"kind", "call_id_sha256"}:
            raise CertificateError(f"Invalid tool linkage event for {label}.")
        digest = event.get("call_id_sha256")
        if event.get("kind") not in {"call", "output"} or not (
            digest == "INVALID" or isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise CertificateError(f"Invalid tool linkage value for {label}.")


def _validate_identifier_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(
        identifier == "INVALID"
        or isinstance(identifier, str)
        and re.fullmatch(r"[0-9a-f]{64}", identifier)
        for identifier in value
    ):
        raise CertificateError(f"Invalid {label}.")
    return cast(list[str], copy.deepcopy(value))


def _validate_exception(value: Any, label: str) -> dict[str, JsonValue] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"type", "message_sha256", "origin"}:
        raise CertificateError(f"Invalid exception for {label}.")
    if not isinstance(value.get("type"), str) or not value.get("type"):
        raise CertificateError(f"Invalid exception type for {label}.")
    if value.get("origin") not in {"runner", "transition", "observation"}:
        raise CertificateError(f"Invalid exception origin for {label}.")
    digest = value.get("message_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise CertificateError(f"Invalid exception digest for {label}.")
    return cast(dict[str, JsonValue], copy.deepcopy(value))


def _validate_state_transition(value: Any, label: str) -> dict[str, JsonValue]:
    required = {
        "kind",
        "source_phase",
        "json_round_trip_requested",
        "json_round_trip_equal",
        "restored_state_equal",
        "state_schema_version",
        "source_interruption_call_ids",
        "restored_interruption_call_ids",
        "decisions",
    }
    fork_fields = {
        "subject_state_before_sha256",
        "subject_state_after_sha256",
        "subject_state_unchanged",
        "sibling_interruption_call_ids",
        "sibling_decisions",
    }
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not set(value).issubset(required | fork_fields)
    ):
        raise CertificateError(f"Invalid {label} fields.")
    present_fork_fields = set(value) & fork_fields
    if present_fork_fields and present_fork_fields != fork_fields:
        raise CertificateError(f"Invalid partial {label} state-fork fields.")
    if value.get("kind") not in {"literal", "resume"}:
        raise CertificateError(f"Invalid {label}.kind.")
    source = value.get("source_phase")
    if source is not None and (not isinstance(source, str) or not source):
        raise CertificateError(f"Invalid {label}.source_phase.")
    if not isinstance(value.get("json_round_trip_requested"), bool):
        raise CertificateError(f"Invalid {label}.json_round_trip_requested.")
    for key in ("json_round_trip_equal", "restored_state_equal"):
        item = value.get(key)
        if item is not None and not isinstance(item, bool):
            raise CertificateError(f"Invalid {label}.{key}.")
    schema_version = value.get("state_schema_version")
    if schema_version is not None and (not isinstance(schema_version, str) or not schema_version):
        raise CertificateError(f"Invalid {label}.state_schema_version.")
    _validate_identifier_list(
        value.get("source_interruption_call_ids"),
        f"{label}.source_interruption_call_ids",
    )
    _validate_identifier_list(
        value.get("restored_interruption_call_ids"),
        f"{label}.restored_interruption_call_ids",
    )
    _validate_transition_decisions(value.get("decisions"), label, field="decisions")
    if present_fork_fields:
        for key in ("subject_state_before_sha256", "subject_state_after_sha256"):
            digest = value.get(key)
            if digest is not None and (
                not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise CertificateError(f"Invalid {label}.{key}.")
        unchanged = value.get("subject_state_unchanged")
        if unchanged is not None and not isinstance(unchanged, bool):
            raise CertificateError(f"Invalid {label}.subject_state_unchanged.")
        sibling_ids = _validate_identifier_list(
            value.get("sibling_interruption_call_ids"),
            f"{label}.sibling_interruption_call_ids",
        )
        sibling_decisions = _validate_transition_decisions(
            value.get("sibling_decisions"),
            label,
            field="sibling_decisions",
        )
        if sibling_decisions:
            if (
                not sibling_ids
                or not isinstance(value.get("subject_state_before_sha256"), str)
                or not isinstance(value.get("subject_state_after_sha256"), str)
                or not isinstance(unchanged, bool)
            ):
                raise CertificateError(f"Incomplete {label} state-fork observation.")
        elif (
            sibling_ids
            or value.get("subject_state_before_sha256") is not None
            or value.get("subject_state_after_sha256") is not None
            or unchanged is not None
        ):
            raise CertificateError(f"Unexpected {label} state-fork observation.")
    return cast(dict[str, JsonValue], copy.deepcopy(value))


def _validate_transition_decisions(
    value: Any,
    label: str,
    *,
    field: str,
) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        raise CertificateError(f"Invalid {label}.{field}.")
    for decision in value:
        if not isinstance(decision, dict) or set(decision) != {
            "action",
            "call_id_sha256",
            "matched",
        }:
            raise CertificateError(f"Invalid {label}.{field} fields.")
        digest = decision.get("call_id_sha256")
        if (
            decision.get("action") not in {"approve", "reject"}
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(decision.get("matched"), bool)
        ):
            raise CertificateError(f"Invalid {label}.{field} entry.")
    return cast(list[dict[str, JsonValue]], copy.deepcopy(value))


def _validate_probe_mapping(value: Any, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and bool(name) and not any(character.isspace() for character in name)
        for name in value
    ):
        raise CertificateError(f"Invalid {label}.")
    return cast(dict[str, JsonValue], copy.deepcopy(value))


def _validate_observation_fields(variant: str, observation: dict[str, Any]) -> None:
    last_agent = observation.get("last_agent")
    if last_agent is not None and (not isinstance(last_agent, str) or not last_agent):
        raise CertificateError(f"Invalid last_agent for {variant}.")
    for key in ("new_items", "model_calls", "session_operations", "stream_event_types"):
        if not isinstance(observation.get(key), list):
            raise CertificateError(f"Invalid {key} for {variant}.")
    session_items = observation.get("session_items")
    if session_items is not None and not isinstance(session_items, list):
        raise CertificateError(f"Invalid session_items for {variant}.")
    stream_events = cast(list[Any], observation["stream_event_types"])
    if not all(isinstance(item, str) and item for item in stream_events):
        raise CertificateError(f"Invalid stream event type for {variant}.")
    tool_counts = observation.get("tool_counts")
    if not isinstance(tool_counts, dict) or not all(
        isinstance(name, str)
        and bool(name)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
        for name, count in tool_counts.items()
    ):
        raise CertificateError(f"Invalid tool_counts for {variant}.")
    linkage = observation.get("tool_linkage")
    if not isinstance(linkage, dict) or set(linkage) != {
        "generated",
        "session",
        "model_inputs",
    }:
        raise CertificateError(f"Invalid tool_linkage for {variant}.")
    generated = linkage.get("generated")
    session = linkage.get("session")
    model_inputs = linkage.get("model_inputs")
    if not isinstance(generated, list):
        raise CertificateError(f"Invalid generated tool linkage for {variant}.")
    if session is not None and not isinstance(session, list):
        raise CertificateError(f"Invalid session tool linkage for {variant}.")
    if not isinstance(model_inputs, list) or not all(
        isinstance(events, list) for events in model_inputs
    ):
        raise CertificateError(f"Invalid model-input tool linkage for {variant}.")
    channels = [generated]
    if isinstance(session, list):
        channels.append(session)
    channels.extend(cast(list[list[Any]], model_inputs))
    for event in [item for channel in channels for item in channel]:
        if not isinstance(event, dict) or set(event) != {"kind", "call_id_sha256"}:
            raise CertificateError(f"Invalid tool linkage event for {variant}.")
        digest = event.get("call_id_sha256")
        if event.get("kind") not in {"call", "output"} or not (
            digest == "INVALID" or isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise CertificateError(f"Invalid tool linkage value for {variant}.")
    interruption_call_ids = observation.get("interruption_call_ids")
    if not isinstance(interruption_call_ids, list) or not all(
        identifier == "INVALID"
        or isinstance(identifier, str)
        and re.fullmatch(r"[0-9a-f]{64}", identifier)
        for identifier in interruption_call_ids
    ):
        raise CertificateError(f"Invalid interruption_call_ids for {variant}.")
    interruption_count = observation.get("interruption_count")
    if (
        not isinstance(interruption_count, int)
        or isinstance(interruption_count, bool)
        or interruption_count < 0
    ):
        raise CertificateError(f"Invalid interruption_count for {variant}.")
    exception = observation.get("exception")
    if exception is not None:
        if not isinstance(exception, dict) or set(exception) != {
            "type",
            "message_sha256",
            "origin",
        }:
            raise CertificateError(f"Invalid exception for {variant}.")
        if not isinstance(exception.get("type"), str) or not exception.get("type"):
            raise CertificateError(f"Invalid exception type for {variant}.")
        if exception.get("origin") not in {"runner", "transition", "observation"}:
            raise CertificateError(f"Invalid exception origin for {variant}.")
        message_digest = exception.get("message_sha256")
        if not isinstance(message_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", message_digest):
            raise CertificateError(f"Invalid exception digest for {variant}.")
    expected_status = "ERROR" if exception is not None else "PASS"
    if observation.get("status") != expected_status:
        raise CertificateError(f"Observation status/exception mismatch for {variant}.")


def _validate_invariants(value: Any) -> tuple[list[str], set[str]]:
    if not isinstance(value, list) or not value:
        raise CertificateError("invariants must be a non-empty list.")
    names: set[str] = set()
    ordered_names: list[str] = []
    statuses: set[str] = set()
    for result in value:
        if not isinstance(result, dict) or set(result) != {
            "name",
            "status",
            "reason",
            "details",
        }:
            raise CertificateError("Invalid invariant result.")
        name = result.get("name")
        status = result.get("status")
        if not isinstance(name, str) or not name or name in names:
            raise CertificateError("Invariant names must be non-empty and unique.")
        if status not in {"PASS", "FAIL", "NOT_RUN"}:
            raise CertificateError(f"Invalid invariant status for {name}.")
        if not isinstance(result.get("reason"), str) or not result.get("reason"):
            raise CertificateError(f"Invalid invariant reason for {name}.")
        names.add(name)
        ordered_names.append(name)
        statuses.add(status)
    return ordered_names, statuses


def _normalized_input_digest(
    *,
    scenario_id: str,
    revision: int,
    phase_contracts: dict[str, JsonValue] | dict[str, Any],
    observations: dict[str, dict[str, JsonValue]] | dict[str, Any],
) -> str:
    model_calls: dict[str, Any] = {}
    for variant in sorted(observations):
        observation = _require_mapping(observations[variant], f"observation {variant}")
        calls = observation.get("model_calls")
        if not isinstance(calls, list):
            raise CertificateError(f"Invalid model_calls for {variant}.")
        model_calls[variant] = calls
    return sha256_hex(
        {
            "scenario": scenario_id,
            "revision": revision,
            "phase_contracts": phase_contracts,
            "model_calls": model_calls,
        }
    )


def _validate_normalized_input_digest(scenario: dict[str, Any], observations_value: Any) -> None:
    observations = _require_mapping(observations_value, "observations")
    phase_contracts = _require_mapping(scenario.get("phase_contracts"), "phase_contracts")
    expected = _normalized_input_digest(
        scenario_id=cast(str, scenario["id"]),
        revision=cast(int, scenario["revision"]),
        phase_contracts=phase_contracts,
        observations=observations,
    )
    if scenario.get("normalized_input_sha256") != expected:
        raise CertificateError("normalized_input_sha256 does not match observations.")


def _validate_expected_outcome(value: Any) -> None:
    _decode_expected_outcome(value, "expected_outcome")


def _decode_expected_outcome(value: Any, label: str) -> ExpectedOutcome:
    outcome = _require_mapping(value, label)
    if set(outcome) != {"kind", "interruption_count", "exception_type"}:
        raise CertificateError(f"Invalid {label} fields.")
    interruption_count = outcome.get("interruption_count")
    exception_type = outcome.get("exception_type")
    if not isinstance(interruption_count, int) or isinstance(interruption_count, bool):
        raise CertificateError(f"Invalid {label} interruption count.")
    if exception_type is not None and not isinstance(exception_type, str):
        raise CertificateError(f"Invalid {label} exception type.")
    try:
        return ExpectedOutcome(
            kind=OutcomeKind(outcome.get("kind")),
            interruption_count=interruption_count,
            exception_type=exception_type,
        )
    except (TypeError, ValueError) as error:
        raise CertificateError(f"Invalid {label} contract.") from error


def _validate_recomputed_invariants(
    *,
    scenario_value: dict[str, Any],
    redaction_value: Any,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]],
    encoded_results: Any,
) -> None:
    expected = _recomputed_invariant_results(
        scenario_value=scenario_value,
        redaction_value=redaction_value,
        observations=observations,
        phase_contracts=phase_contracts,
    )
    if encoded_results != expected:
        raise CertificateError("Invariant results do not match recomputed observations.")


def _recomputed_invariant_results(
    *,
    scenario_value: dict[str, Any],
    redaction_value: Any,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]],
) -> list[dict[str, JsonValue]]:
    redaction = _require_mapping(redaction_value, "redaction")
    scenario = Scenario(
        scenario_id=cast(str, scenario_value["id"]),
        revision=cast(int, scenario_value["revision"]),
        description=cast(str, scenario_value["description"]),
        variants=tuple(RunVariant(item) for item in cast(list[str], scenario_value["variants"])),
        invariants=tuple(cast(list[str], scenario_value["requested_invariants"])),
        factory=_certificate_factory,
        expected_tool_counts=cast(dict[str, int], scenario_value["expected_tool_counts"]),
        expected_outcome=ExpectedOutcome(
            kind=OutcomeKind(
                cast(str, cast(dict[str, Any], scenario_value["expected_outcome"])["kind"])
            ),
            interruption_count=cast(
                int,
                cast(dict[str, Any], scenario_value["expected_outcome"])["interruption_count"],
            ),
            exception_type=cast(
                str | None,
                cast(dict[str, Any], scenario_value["expected_outcome"])["exception_type"],
            ),
        ),
        public_payloads=cast(bool, redaction["public_payloads"]),
    )
    return [
        result.as_json() for result in evaluate_invariants(scenario, observations, phase_contracts)
    ]


def _sanitize_observation_metadata(
    observations: dict[str, dict[str, JsonValue]], *, public: bool
) -> dict[str, dict[str, JsonValue]]:
    sanitized = copy.deepcopy(observations)
    if public:
        return sanitized
    for variant_name, observation in sanitized.items():
        phases = observation.get("phases")
        if (
            not isinstance(phases, list)
            or not phases
            or not all(isinstance(phase, dict) for phase in phases)
        ):
            raise CertificateError(f"Invalid phase observations for {variant_name}.")
        sanitized_phases = [
            _sanitize_private_phase_observation(cast(dict[str, JsonValue], phase))
            for phase in phases
        ]
        decoded_phases = tuple(
            _decode_phase_observation(phase, variant=variant_name, index=index)
            for index, phase in enumerate(sanitized_phases)
        )
        try:
            variant = RunVariant(variant_name)
        except ValueError as error:
            raise CertificateError(f"Unsupported observation variant {variant_name}.") from error
        sanitized[variant_name] = _aggregate_encoded_observation(
            variant=variant,
            phases=decoded_phases,
            public_payloads=False,
        )
    return sanitized


def _sanitize_private_phase_observation(
    phase: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    sanitized = copy.deepcopy(phase)
    for key in ("phase_id", "model_group", "last_agent"):
        value = sanitized.get(key)
        if isinstance(value, str):
            sanitized[key] = _public_name(value, public=False)
    sanitized["final_output"] = _ensure_redacted_summary(sanitized.get("final_output"))
    for key in (
        "new_items",
        "model_calls",
        "session_operations",
        "session_items_before",
        "session_items_after",
    ):
        values = sanitized.get(key)
        if isinstance(values, list):
            sanitized[key] = [_ensure_redacted_summary(item) for item in values]
    counts = sanitized.get("tool_counts_delta")
    if isinstance(counts, dict):
        sanitized["tool_counts_delta"] = {
            _public_name(name, public=False): count
            for name, count in cast(dict[str, int], counts).items()
        }
    exception = sanitized.get("exception")
    if isinstance(exception, dict) and isinstance(exception.get("type"), str):
        exception["type"] = _public_name(cast(str, exception["type"]), public=False)
    sanitized["usage"] = _ensure_redacted_summary(sanitized.get("usage"))
    sanitized["guardrails"] = _ensure_redacted_summary(sanitized.get("guardrails"))
    event_types = sanitized.get("stream_event_types")
    if isinstance(event_types, list):
        sanitized["stream_event_types"] = [
            _public_name(item, public=False) for item in event_types if isinstance(item, str)
        ]
    for key in ("probes_before", "probes_after"):
        probes = sanitized.get(key)
        if isinstance(probes, dict):
            sanitized[key] = {
                _public_name(name, public=False): _ensure_redacted_summary(value)
                for name, value in cast(dict[str, Any], probes).items()
            }
    transition = sanitized.get("state_transition")
    if isinstance(transition, dict):
        source_phase = transition.get("source_phase")
        if isinstance(source_phase, str):
            transition["source_phase"] = _public_name(source_phase, public=False)
        schema_version = transition.get("state_schema_version")
        if isinstance(schema_version, str):
            transition["state_schema_version"] = _public_name(schema_version, public=False)
    return sanitized


def _ensure_redacted_summary(value: Any) -> dict[str, JsonValue]:
    if _is_redacted_summary(value):
        return cast(dict[str, JsonValue], copy.deepcopy(value))
    return _redacted_summary(value)


def _public_name(value: str, *, public: bool) -> str:
    return value if public else f"sha256:{sha256_hex(value)}"


def _redacted_summary(value: Any) -> dict[str, JsonValue]:
    payload = to_json_value(value)
    encoded = canonical_bytes(payload)
    return {
        "redacted": True,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "kind": _json_kind(payload),
    }


def _json_kind(value: JsonValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _certificate_factory(variant: RunVariant) -> ScenarioCase:
    del variant
    raise CertificateError("A checked certificate cannot execute its scenario factory.")


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CertificateError(f"{label} must be an object.")
    return cast(dict[str, Any], value)


def _validate_redaction(
    value: Any, *, scenario: dict[str, Any], observations: dict[str, Any]
) -> None:
    if not isinstance(value, dict) or set(value) != {"public_payloads", "policy"}:
        raise CertificateError("Invalid redaction metadata.")
    public = value.get("public_payloads")
    policy = value.get("policy")
    if not isinstance(public, bool):
        raise CertificateError("redaction.public_payloads must be boolean.")
    expected = "public-synthetic-v1" if public else "sha256-summary-v1"
    if policy != expected:
        raise CertificateError("Redaction policy is inconsistent with public_payloads.")
    if not public:
        _validate_private_payload_shapes(scenario=scenario, observations=observations)


def _validate_private_payload_shapes(
    *, scenario: dict[str, Any], observations: dict[str, Any]
) -> None:
    for key in ("id", "description"):
        if not _is_name_digest(scenario.get(key)):
            raise CertificateError(f"Private scenario.{key} must be a SHA-256 name digest.")
    requested = scenario.get("requested_invariants")
    if not isinstance(requested, list) or not all(
        name in _PRIVATE_SAFE_INVARIANTS or _is_name_digest(name) for name in requested
    ):
        raise CertificateError("Private requested invariant names are not safely represented.")
    expected_counts = scenario.get("expected_tool_counts")
    if not isinstance(expected_counts, dict) or not all(
        _is_name_digest(name) for name in expected_counts
    ):
        raise CertificateError("Private expected tool names must be SHA-256 name digests.")
    expected_outcome = scenario.get("expected_outcome")
    if isinstance(expected_outcome, dict):
        exception_type = expected_outcome.get("exception_type")
        if exception_type is not None and not _is_name_digest(exception_type):
            raise CertificateError("Private expected exception type must be a SHA-256 name digest.")
    _validate_private_phase_contract_shapes(scenario.get("phase_contracts"))

    for variant, raw_observation in observations.items():
        if not isinstance(raw_observation, dict):
            raise CertificateError(f"Invalid private observation for {variant}.")
        for key in ("final_output", "usage", "guardrails"):
            if not _is_redacted_summary(raw_observation.get(key)):
                raise CertificateError(f"Private observation {variant}.{key} is not redacted.")
        last_agent = raw_observation.get("last_agent")
        if last_agent is not None and not _is_name_digest(last_agent):
            raise CertificateError(f"Private observation {variant}.last_agent is not redacted.")
        for key in ("new_items", "model_calls", "session_operations"):
            values = raw_observation.get(key)
            if not isinstance(values, list) or not all(
                _is_redacted_summary(item) for item in values
            ):
                raise CertificateError(f"Private observation {variant}.{key} is not redacted.")
        session_items = raw_observation.get("session_items")
        if session_items is not None and (
            not isinstance(session_items, list)
            or not all(_is_redacted_summary(item) for item in session_items)
        ):
            raise CertificateError(f"Private observation {variant}.session_items is not redacted.")
        tool_counts = raw_observation.get("tool_counts")
        if not isinstance(tool_counts, dict) or not all(
            _is_name_digest(name) for name in tool_counts
        ):
            raise CertificateError(f"Private observation {variant}.tool_counts is not redacted.")
        event_types = raw_observation.get("stream_event_types")
        if not isinstance(event_types, list) or not all(
            _is_name_digest(item) for item in event_types
        ):
            raise CertificateError(
                f"Private observation {variant}.stream_event_types is not redacted."
            )
        exception = raw_observation.get("exception")
        if isinstance(exception, dict) and not _is_name_digest(exception.get("type")):
            raise CertificateError(f"Private observation {variant}.exception type is not redacted.")
        phases = raw_observation.get("phases")
        if not isinstance(phases, list) or not phases:
            raise CertificateError(f"Private observation {variant}.phases is invalid.")
        for index, phase in enumerate(phases):
            _validate_private_phase_observation_shape(
                phase, label=f"Private observation {variant}.phases[{index}]"
            )


def _validate_private_phase_contract_shapes(value: Any) -> None:
    if not isinstance(value, dict):
        raise CertificateError("Private phase_contracts must be an object.")
    for variant, contracts in value.items():
        if not isinstance(contracts, list):
            raise CertificateError(f"Private phase contracts for {variant} must be an array.")
        for index, contract in enumerate(contracts):
            label = f"Private phase_contracts.{variant}[{index}]"
            if not isinstance(contract, dict):
                raise CertificateError(f"{label} must be an object.")
            for key in ("phase_id", "model_group"):
                if not _is_name_digest(contract.get(key)):
                    raise CertificateError(f"{label}.{key} must be a SHA-256 name digest.")
            source = contract.get("source_phase")
            if source is not None and not _is_name_digest(source):
                raise CertificateError(f"{label}.source_phase must be a SHA-256 name digest.")
            outcome = contract.get("expected_outcome")
            if isinstance(outcome, dict):
                exception_type = outcome.get("exception_type")
                if exception_type is not None and not _is_name_digest(exception_type):
                    raise CertificateError(
                        f"{label}.expected_outcome exception type is not redacted."
                    )
            for key in ("expected_tool_counts_delta", "expected_probes_after"):
                mapping = contract.get(key)
                if not isinstance(mapping, dict) or not all(
                    _is_name_digest(name) for name in mapping
                ):
                    raise CertificateError(f"{label}.{key} names are not redacted.")
                if key == "expected_probes_after" and not all(
                    _is_redacted_summary(probe) for probe in mapping.values()
                ):
                    raise CertificateError(f"{label}.{key} values are not redacted.")
            callbacks = contract.get("callback_markers")
            if not isinstance(callbacks, dict):
                raise CertificateError(f"{label}.callback_markers is invalid.")
            before = callbacks.get("before")
            if before is not None and not _is_name_digest(before):
                raise CertificateError(f"{label}.callback before marker is not redacted.")
            probes = callbacks.get("probes")
            if not isinstance(probes, dict) or not all(
                _is_name_digest(name) and _is_name_digest(marker) for name, marker in probes.items()
            ):
                raise CertificateError(f"{label}.callback probe markers are not redacted.")
            for key in ("decisions", "sibling_decisions"):
                decisions = contract.get(key, [])
                if not isinstance(decisions, list) or not all(
                    isinstance(decision, dict)
                    and _is_redacted_summary(decision.get("rejection_message"))
                    for decision in decisions
                ):
                    raise CertificateError(f"{label}.{key} messages are not redacted.")


def _validate_private_phase_observation_shape(value: Any, *, label: str) -> None:
    if not isinstance(value, dict):
        raise CertificateError(f"{label} must be an object.")
    for key in ("phase_id", "model_group"):
        if not _is_name_digest(value.get(key)):
            raise CertificateError(f"{label}.{key} is not redacted.")
    last_agent = value.get("last_agent")
    if last_agent is not None and not _is_name_digest(last_agent):
        raise CertificateError(f"{label}.last_agent is not redacted.")
    for key in ("final_output", "usage", "guardrails"):
        if not _is_redacted_summary(value.get(key)):
            raise CertificateError(f"{label}.{key} is not redacted.")
    for key in (
        "new_items",
        "model_calls",
        "session_operations",
        "session_items_before",
        "session_items_after",
    ):
        items = value.get(key)
        if items is None and key.startswith("session_items_"):
            continue
        if not isinstance(items, list) or not all(_is_redacted_summary(item) for item in items):
            raise CertificateError(f"{label}.{key} is not redacted.")
    counts = value.get("tool_counts_delta")
    if not isinstance(counts, dict) or not all(_is_name_digest(name) for name in counts):
        raise CertificateError(f"{label}.tool_counts_delta names are not redacted.")
    event_types = value.get("stream_event_types")
    if not isinstance(event_types, list) or not all(_is_name_digest(item) for item in event_types):
        raise CertificateError(f"{label}.stream_event_types is not redacted.")
    exception = value.get("exception")
    if isinstance(exception, dict) and not _is_name_digest(exception.get("type")):
        raise CertificateError(f"{label}.exception type is not redacted.")
    for key in ("probes_before", "probes_after"):
        probes = value.get(key)
        if not isinstance(probes, dict) or not all(
            _is_name_digest(name) and _is_redacted_summary(probe) for name, probe in probes.items()
        ):
            raise CertificateError(f"{label}.{key} is not redacted.")
    transition = value.get("state_transition")
    if not isinstance(transition, dict):
        raise CertificateError(f"{label}.state_transition is invalid.")
    source = transition.get("source_phase")
    if source is not None and not _is_name_digest(source):
        raise CertificateError(f"{label}.state_transition source is not redacted.")
    schema_version = transition.get("state_schema_version")
    if schema_version is not None and not _is_name_digest(schema_version):
        raise CertificateError(f"{label}.state schema version is not redacted.")


def _is_name_digest(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


def _is_redacted_summary(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"redacted", "sha256", "bytes", "kind"}
        and value.get("redacted") is True
        and isinstance(value.get("sha256"), str)
        and bool(re.fullmatch(r"[0-9a-f]{64}", cast(str, value.get("sha256"))))
        and isinstance(value.get("bytes"), int)
        and not isinstance(value.get("bytes"), bool)
        and cast(int, value.get("bytes")) >= 0
        and value.get("kind") in {"null", "boolean", "number", "string", "array", "object"}
    )


def schema_path() -> Path:
    return Path(__file__).resolve().parent / "schemas" / "certificate-v1.schema.json"


def write_certificate(path: Path, certificate: dict[str, JsonValue]) -> None:
    rendered = certificate_json(certificate)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        try:
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_descriptor = None
        if directory_descriptor is not None:
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()
