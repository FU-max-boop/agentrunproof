from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, cast

from .._canonical import CanonicalizationError, JsonValue, sha256_hex
from ..certificate import CertificateError, validate_certificate
from ..history.bundle import validate_environment

BUNDLE_SCHEMA_VERSION: Final = "agentrunproof.current-bundle/v1"
CASE_ID: Final = "runstate-sibling-approval-isolation"
CASE_REVISION: Final = 1
ENVIRONMENT_ID: Final = "sha256:06abc42c3fedc18e2e96891fdc19a3ae70bf210eea635aeb3f7b5b701a2e84d5"
NORMALIZED_INPUT_SHA256: Final = "0b8f9575ccdd745bf809f2c969d5cdd4021191d561c83925ca557c7370e1979b"
CALL_ID_SHA256: Final = "7ca8ed67e3ea51465df83f68ddd7842a04dc7e8846c314c9fbfde4c9588da1e7"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_WHEEL = re.compile(r"^agentrunproof-(?P<version>[A-Za-z0-9][A-Za-z0-9_.!+]*)-py3-none-any\.whl$")

_BUNDLE_KEYS = {
    "schema_version",
    "bundle_id",
    "source",
    "runtime",
    "wheel",
    "environment",
    "certificate",
    "limitations",
}
_SOURCE_KEYS = {"commit", "tree", "clean", "index_flags_clean"}
_RUNTIME_KEYS = {
    "python",
    "implementation",
    "system",
    "machine",
    "fresh_venvs",
    "python_isolated",
    "network_guard",
    "worker_cwd",
    "environment_sanitized",
    "worker_timeout_seconds",
}
_MEMBER_KEYS = {"path", "size", "sha256"}
_CERTIFICATE_MEMBER_KEYS = {*_MEMBER_KEYS, "certificate_id"}
_VARIANTS = ("non_streaming", "streaming")
_REQUESTED_INVARIANTS = (
    "execution_outcome",
    "state_transitions",
    "state_fork_isolation",
    "phase_contract",
    "tool_linkage",
    "exactly_once",
    "stream_parity",
)
_INVARIANT_FINGERPRINT = (
    ("execution_outcome", "FAIL", "UNEXPECTED_EXECUTION_OUTCOME"),
    ("state_transitions", "PASS", "OK"),
    ("state_fork_isolation", "FAIL", "SIBLING_STATE_MUTATED"),
    ("phase_contract", "FAIL", "PHASE_OBSERVATION_MISMATCH"),
    ("tool_linkage", "PASS", "OK"),
    ("exactly_once", "FAIL", "SIDE_EFFECT_COUNT_MISMATCH"),
    ("stream_parity", "PASS", "OK"),
)
_LIMITATIONS = [
    "This bundle is content-addressed and Git-anchored, not cryptographically signed.",
    "The socket-deny guard covers Python worker execution, not artifact acquisition.",
    "The AgentRunProof wheel is hash-bound and published with CI/release artifacts, not committed beside this marker.",
    "This certificate binds released openai-agents 0.20.0; it does not by itself attest to unreleased source revisions.",
]


class CurrentBundleError(ValueError):
    """Raised when current counterexample evidence is malformed or semantically false."""


def limitations() -> list[str]:
    return list(_LIMITATIONS)


def validate_current_certificate(value: Any) -> dict[str, JsonValue]:
    """Validate certificate v1 plus the exact released sibling-isolation fingerprint."""

    try:
        certificate = validate_certificate(value)
    except CertificateError as error:
        raise CurrentBundleError(f"Invalid certificate: {error}") from error

    _expect(certificate.get("overall_status"), "FAIL", "certificate.overall_status")
    tool = _object(certificate.get("tool"), "certificate.tool")
    _expect(tool.get("name"), "agentrunproof", "certificate.tool.name")

    runtime = _object(certificate.get("runtime"), "certificate.runtime")
    _expect(runtime.get("python"), "3.12.13", "certificate.runtime.python")
    _expect(runtime.get("implementation"), "CPython", "certificate.runtime.implementation")
    _expect(
        runtime.get("platform"),
        {"system": "Linux", "machine": "x86_64"},
        "certificate.runtime.platform",
    )
    _expect(
        runtime.get("packages"),
        {
            "openai-agents": "0.20.0",
            "openai": "2.54.0",
            "pydantic": "2.13.4",
        },
        "certificate.runtime.packages",
    )

    source = _object(certificate.get("source"), "certificate.source")
    _expect(source.get("dirty"), False, "certificate.source.dirty")

    scenario = _object(certificate.get("scenario"), "certificate.scenario")
    _expect(scenario.get("id"), CASE_ID, "certificate.scenario.id")
    _expect(scenario.get("revision"), CASE_REVISION, "certificate.scenario.revision")
    _expect(scenario.get("variants"), list(_VARIANTS), "certificate.scenario.variants")
    _expect(
        scenario.get("requested_invariants"),
        list(_REQUESTED_INVARIANTS),
        "certificate.scenario.requested_invariants",
    )
    _expect(
        scenario.get("normalized_input_sha256"),
        NORMALIZED_INPUT_SHA256,
        "certificate.scenario.normalized_input_sha256",
    )
    _expect(
        certificate.get("redaction"),
        {"public_payloads": True, "policy": "public-synthetic-v1"},
        "certificate.redaction",
    )

    invariants = certificate.get("invariants")
    if not isinstance(invariants, list):
        raise CurrentBundleError("certificate.invariants must be an array.")
    fingerprint: list[tuple[Any, Any, Any]] = []
    for index, result in enumerate(invariants):
        item = _object(result, f"certificate.invariants[{index}]")
        fingerprint.append((item.get("name"), item.get("status"), item.get("reason")))
    if tuple(fingerprint) != _INVARIANT_FINGERPRINT:
        raise CurrentBundleError("Certificate invariant fingerprint does not match the case.")

    observations = _object(certificate.get("observations"), "certificate.observations")
    _exact_keys(observations, set(_VARIANTS), "certificate.observations")
    for variant in _VARIANTS:
        _validate_variant_fingerprint(
            _object(observations.get(variant), f"certificate.observations.{variant}"),
            variant=variant,
        )
    return certificate


def finalize_current_certificate(value: Any, *, source_commit: str) -> dict[str, JsonValue]:
    """Bind a wheel-executed certificate to a twice-checked clean source commit."""

    if not _GIT_OBJECT.fullmatch(source_commit):
        raise CurrentBundleError("source_commit must be a 40-character Git object ID.")
    try:
        certificate = validate_certificate(value)
    except CertificateError as error:
        raise CurrentBundleError(f"Invalid unbound certificate: {error}") from error
    source = _object(certificate.get("source"), "certificate.source")
    expected_unavailable = {
        "commit": None,
        "dirty": None,
        "tracked_diff_sha256": None,
        "untracked_paths_sha256": None,
        "index_flags_sha256": None,
    }
    _expect(source, expected_unavailable, "unbound certificate.source")

    candidate = copy.deepcopy(certificate)
    candidate["source"] = {
        "commit": source_commit,
        "dirty": False,
        "tracked_diff_sha256": sha256_hex(""),
        "untracked_paths_sha256": sha256_hex([]),
        "index_flags_sha256": sha256_hex([]),
    }
    candidate["certificate_id"] = None
    candidate["certificate_id"] = _content_id(candidate, "certificate_id")
    return validate_current_certificate(candidate)


def parse_current_certificate_json(text: str) -> dict[str, JsonValue]:
    """Parse one strict certificate snapshot and validate its current-case fingerprint."""

    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except CurrentBundleError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise CurrentBundleError(f"Cannot parse current certificate: {error}") from error
    return validate_current_certificate(value)


def finalize_current_bundle(value: Any, *, directory: Path) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise CurrentBundleError("The current evidence bundle must be a JSON object.")
    candidate = copy.deepcopy(value)
    if "bundle_id" not in candidate:
        candidate["bundle_id"] = None
    elif candidate["bundle_id"] is not None:
        raise CurrentBundleError("A bundle being finalized must have a null bundle_id.")
    candidate["bundle_id"] = _content_id(candidate, "bundle_id")
    return validate_current_bundle(candidate, directory=directory)


def validate_current_bundle(value: Any, *, directory: Path) -> dict[str, JsonValue]:
    bundle = _object(value, "current evidence bundle")
    _exact_keys(bundle, _BUNDLE_KEYS, "current evidence bundle")
    _expect(bundle.get("schema_version"), BUNDLE_SCHEMA_VERSION, "schema_version")
    bundle_id = bundle.get("bundle_id")
    if not isinstance(bundle_id, str) or not _PREFIXED_SHA256.fullmatch(bundle_id):
        raise CurrentBundleError("bundle_id must be a lowercase SHA-256 identifier.")
    if bundle_id != _content_id(bundle, "bundle_id"):
        raise CurrentBundleError("bundle_id does not match the canonical bundle payload.")

    source = _validate_source(bundle.get("source"))
    runtime_python = _validate_runtime(bundle.get("runtime"))
    wheel = _validate_file_member(bundle.get("wheel"), label="wheel", expected_path=None)
    wheel_name = cast(str, wheel["path"])
    wheel_match = _WHEEL.fullmatch(wheel_name)
    if wheel_match is None:
        raise CurrentBundleError("wheel.path must be the canonical AgentRunProof wheel name.")
    _validate_optional_member(directory / wheel_name, wheel, label="wheel")

    try:
        environment = validate_environment(bundle.get("environment"))
    except ValueError as error:
        raise CurrentBundleError(f"Invalid environment closure: {error}") from error
    _expect(environment.get("environment_id"), ENVIRONMENT_ID, "environment.environment_id")
    _expect(environment.get("sdk_version"), "0.20.0", "environment.sdk_version")
    _expect(environment.get("python"), runtime_python, "environment.python")

    certificate_member = _validate_file_member(
        bundle.get("certificate"),
        label="certificate",
        expected_path="certificate.json",
        keys=_CERTIFICATE_MEMBER_KEYS,
    )
    certificate_path = directory / "certificate.json"
    certificate_bytes = _required_member_bytes(certificate_path, label="certificate")
    _expect(len(certificate_bytes), certificate_member["size"], "certificate.size")
    _expect(
        hashlib.sha256(certificate_bytes).hexdigest(),
        certificate_member["sha256"],
        "certificate.sha256",
    )
    certificate = _parse_current_certificate_bytes(certificate_bytes)
    _expect(
        certificate.get("certificate_id"),
        certificate_member.get("certificate_id"),
        "certificate.certificate_id",
    )

    certificate_source = _object(certificate.get("source"), "certificate.source")
    _expect(certificate_source.get("commit"), source["commit"], "certificate.source.commit")
    certificate_tool = _object(certificate.get("tool"), "certificate.tool")
    _expect(
        certificate_tool.get("version"),
        wheel_match.group("version"),
        "certificate.tool.version",
    )
    _validate_runtime_relationships(certificate, environment)
    _expect(bundle.get("limitations"), _LIMITATIONS, "limitations")
    return cast(dict[str, JsonValue], copy.deepcopy(bundle))


def load_current_bundle(path: Path) -> dict[str, JsonValue]:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise CurrentBundleError(f"Cannot read current evidence bundle: {error}") from error
    if path.is_symlink() or not path.is_file():
        raise CurrentBundleError("The current evidence bundle must be a regular file.")
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except CurrentBundleError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise CurrentBundleError(f"Cannot parse current evidence bundle: {error}") from error
    return validate_current_bundle(payload, directory=path.parent)


def current_bundle_json(value: Any, *, directory: Path) -> str:
    bundle = validate_current_bundle(value, directory=directory)
    return json.dumps(bundle, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_current_bundle(path: Path, value: Any) -> None:
    rendered = current_bundle_json(value, directory=path.parent)
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


def _validate_variant_fingerprint(value: dict[str, Any], *, variant: str) -> None:
    location = f"certificate.observations.{variant}"
    _expect(
        value.get("final_output"), "unexpected sibling approval leak", f"{location}.final_output"
    )
    _expect(value.get("interruption_count"), 0, f"{location}.interruption_count")
    _expect(value.get("tool_counts"), {"approval_tool": 1}, f"{location}.tool_counts")
    _expect(value.get("remaining_model_steps"), 0, f"{location}.remaining_model_steps")
    _expect(value.get("exception"), None, f"{location}.exception")

    phases = value.get("phases")
    if not isinstance(phases, list) or len(phases) != 2:
        raise CurrentBundleError(f"{location}.phases must contain exactly two phases.")
    initial = _object(phases[0], f"{location}.phases[0]")
    fork = _object(phases[1], f"{location}.phases[1]")
    _expect(initial.get("phase_id"), "initial", f"{location}.phases[0].phase_id")
    _expect(initial.get("interruption_count"), 1, f"{location}.phases[0].interruption_count")
    _expect(
        initial.get("tool_counts_delta"),
        {"approval_tool": 0},
        f"{location}.phases[0].tool_counts_delta",
    )
    _expect(initial.get("remaining_model_steps"), 1, f"{location}.phases[0].remaining_model_steps")
    _expect(initial.get("exception"), None, f"{location}.phases[0].exception")

    _expect(fork.get("phase_id"), "fork-check", f"{location}.phases[1].phase_id")
    _expect(fork.get("interruption_count"), 0, f"{location}.phases[1].interruption_count")
    _expect(
        fork.get("tool_counts_delta"),
        {"approval_tool": 1},
        f"{location}.phases[1].tool_counts_delta",
    )
    _expect(fork.get("remaining_model_steps"), 0, f"{location}.phases[1].remaining_model_steps")
    _expect(fork.get("exception"), None, f"{location}.phases[1].exception")
    _expect(
        fork.get("final_output"),
        "unexpected sibling approval leak",
        f"{location}.phases[1].final_output",
    )

    transition = _object(fork.get("state_transition"), f"{location}.phases[1].state_transition")
    singleton = [CALL_ID_SHA256]
    for field in (
        "source_interruption_call_ids",
        "restored_interruption_call_ids",
        "sibling_interruption_call_ids",
    ):
        _expect(transition.get(field), singleton, f"{location}.phases[1].state_transition.{field}")
    _expect(transition.get("decisions"), [], f"{location}.phases[1].state_transition.decisions")
    _expect(
        transition.get("sibling_decisions"),
        [{"action": "approve", "call_id_sha256": CALL_ID_SHA256, "matched": True}],
        f"{location}.phases[1].state_transition.sibling_decisions",
    )
    before = transition.get("subject_state_before_sha256")
    after = transition.get("subject_state_after_sha256")
    if (
        not isinstance(before, str)
        or not _SHA256.fullmatch(before)
        or not isinstance(after, str)
        or not _SHA256.fullmatch(after)
        or before == after
    ):
        raise CurrentBundleError(f"{location} does not contain the state-mutation fingerprint.")
    _expect(
        transition.get("subject_state_unchanged"),
        False,
        f"{location}.phases[1].state_transition.subject_state_unchanged",
    )


def _validate_source(value: Any) -> dict[str, JsonValue]:
    source = _object(value, "source")
    _exact_keys(source, _SOURCE_KEYS, "source")
    for key in ("commit", "tree"):
        item = source.get(key)
        if not isinstance(item, str) or not _GIT_OBJECT.fullmatch(item):
            raise CurrentBundleError(f"source.{key} must be a 40-character Git object ID.")
    if source.get("clean") is not True or source.get("index_flags_clean") is not True:
        raise CurrentBundleError("Canonical current evidence requires a clean source and index.")
    return cast(dict[str, JsonValue], copy.deepcopy(source))


def _validate_runtime(value: Any) -> str:
    runtime = _object(value, "runtime")
    _exact_keys(runtime, _RUNTIME_KEYS, "runtime")
    expected: dict[str, JsonValue] = {
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
    }
    for key, item in expected.items():
        _expect(runtime.get(key), item, f"runtime.{key}")
    return "3.12.13"


def _validate_runtime_relationships(
    certificate: dict[str, JsonValue], environment: dict[str, JsonValue]
) -> None:
    runtime = _object(certificate.get("runtime"), "certificate.runtime")
    packages = _object(runtime.get("packages"), "certificate.runtime.packages")
    artifacts = environment.get("artifacts")
    if not isinstance(artifacts, list):
        raise CurrentBundleError("environment.artifacts must be an array.")
    versions: dict[str, Any] = {}
    for artifact in artifacts:
        item = _object(artifact, "environment artifact")
        distribution = item.get("distribution")
        if isinstance(distribution, str):
            versions[distribution] = item.get("version")
    for distribution in ("openai-agents", "openai", "pydantic"):
        _expect(
            packages.get(distribution),
            versions.get(distribution),
            f"certificate.runtime.packages.{distribution}",
        )


def _validate_file_member(
    value: Any,
    *,
    label: str,
    expected_path: str | None,
    keys: set[str] = _MEMBER_KEYS,
) -> dict[str, JsonValue]:
    member = _object(value, label)
    _exact_keys(member, keys, label)
    path = member.get("path")
    if not isinstance(path, str) or Path(path).name != path or not path:
        raise CurrentBundleError(f"{label}.path must be a basename.")
    if expected_path is not None and path != expected_path:
        raise CurrentBundleError(f"{label}.path must be {expected_path!r}.")
    size = member.get("size")
    digest = member.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise CurrentBundleError(f"{label}.size must be positive.")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise CurrentBundleError(f"{label}.sha256 must be a lowercase digest.")
    if "certificate_id" in keys:
        identifier = member.get("certificate_id")
        if not isinstance(identifier, str) or not _PREFIXED_SHA256.fullmatch(identifier):
            raise CurrentBundleError("certificate.certificate_id must be a lowercase identifier.")
    return cast(dict[str, JsonValue], copy.deepcopy(member))


def _validate_optional_member(path: Path, member: dict[str, JsonValue], *, label: str) -> None:
    if not path.exists() and not path.is_symlink():
        return
    data = _required_member_bytes(path, label=label)
    _expect(len(data), member["size"], f"{label}.size")
    _expect(hashlib.sha256(data).hexdigest(), member["sha256"], f"{label}.sha256")


def _required_member_bytes(path: Path, *, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise CurrentBundleError(f"The {label} member is missing or is a symbolic link.")
    try:
        return path.read_bytes()
    except OSError as error:
        raise CurrentBundleError(f"Cannot read {label} member: {error}") from error


def _parse_current_certificate_bytes(data: bytes) -> dict[str, JsonValue]:
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        raise CurrentBundleError(f"Cannot parse certificate member: {error}") from error
    return parse_current_certificate_json(text)


def _content_id(value: Any, field: str) -> str:
    if not isinstance(value, dict):
        raise CurrentBundleError("A content-addressed payload must be an object.")
    unsigned = copy.deepcopy(value)
    unsigned[field] = None
    try:
        return f"sha256:{sha256_hex(unsigned)}"
    except CanonicalizationError as error:
        raise CurrentBundleError(f"Bundle payload is not canonical JSON: {error}") from error


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CurrentBundleError(f"{label} must be a JSON object.")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise CurrentBundleError(f"{label} fields differ; missing={missing}, extra={extra}.")


def _expect(actual: Any, expected: Any, label: str) -> None:
    if type(actual) is not type(expected) or actual != expected:
        raise CurrentBundleError(f"{label} does not match the current evidence contract.")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CurrentBundleError(f"Duplicate JSON object key: {key!r}.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise CurrentBundleError(f"Non-finite JSON number is forbidden: {value}.")
