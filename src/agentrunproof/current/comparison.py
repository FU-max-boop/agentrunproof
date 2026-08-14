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
from .bundle import ENVIRONMENT_ID as RELEASE_ENVIRONMENT_ID
from .bundle import validate_current_certificate

COMPARISON_SCHEMA_VERSION: Final = "agentrunproof.upstream-comparison/v1"
UPSTREAM_REPOSITORY: Final = "https://github.com/openai/openai-agents-python"
UPSTREAM_COMMIT: Final = "0b93ce8faa27d4631df399fe48856b52a8fd9897"
UPSTREAM_TREE: Final = "ac2f56fc2a352ead510b1782879702f319e11255"
UPSTREAM_PARENT: Final = "3e87dc8ab154039e59764762155e1f7230950c5f"
UPSTREAM_TRACKED_SOURCE_SHA256: Final = (
    "cd06dfdad164118ae6bdee53f147b10ac3442b026e54458e2dfa6c9df051d3a5"
)
UPSTREAM_PYPROJECT_SHA256: Final = (
    "0e07707646f489428b873ddffaecbb4bddc180dc21dc706d33eab3c388fe0e17"
)
UPSTREAM_UV_LOCK_SHA256: Final = "cdcfdeec08bb320d92f56cf1b91569afde65e928cbb1015b470f78383f14ba14"
UPSTREAM_LOCK_SHA256: Final = "827c04344cec78a32ce561ae2273405758f50403dd375bf86cb8df91830119e7"
RELEASE_WHEEL_SHA256: Final = "aaff662b802fa90762ad539e131b9ea387e12e3664b87bc75157ad1b3fc88850"

RELEASE_TARGET_ID: Final = "pypi-openai-agents-0.20.0"
UPSTREAM_TARGET_ID: Final = "openai-agents-python-0b93ce8"
TOP_LEVEL_CASE_ID: Final = "runstate-sibling-approval-isolation"
RECURSIVE_CASE_ID: Final = "runstate-recursive-agent-tool-approval-routing"
SERIALIZED_RECURSIVE_CASE_ID: Final = "runstate-recursive-agent-tool-approval-serialization"

RELEASED_CERTIFICATE_PATH: Final = "released-top-level-certificate.json"
MERGED_TOP_LEVEL_CERTIFICATE_PATH: Final = "merged-top-level-certificate.json"
MERGED_RECURSIVE_CERTIFICATE_PATH: Final = "merged-recursive-two-edge-certificate.json"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_DIST_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_MEMBER_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.!+~-]*$")
_HARNESS_WHEEL = re.compile(
    r"^agentrunproof-(?P<version>[A-Za-z0-9][A-Za-z0-9_.!+]*)-py3-none-any\.whl$"
)
_SDK_WHEEL = re.compile(r"^openai_agents-0\.20\.0-py3-none-any\.whl$")

_BUNDLE_KEYS = {
    "schema_version",
    "bundle_id",
    "source",
    "runtime",
    "harness",
    "targets",
    "runs",
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
_HARNESS_KEYS = {"wheel"}
_MEMBER_KEYS = {"path", "size", "sha256"}
_CERTIFICATE_MEMBER_KEYS = {*_MEMBER_KEYS, "certificate_id"}
_ARTIFACT_KEYS = {"distribution", "version", "filename", "size", "sha256"}
_DIRECT_URL_KEYS = {"url_scheme", "url_basename", "archive_sha256"}
_RELEASE_TARGET_KEYS = {
    "target_id",
    "kind",
    "distribution",
    "version",
    "wheel",
    "environment",
}
_UPSTREAM_TARGET_KEYS = {
    "target_id",
    "kind",
    "distribution",
    "version",
    "repository",
    "commit",
    "tree",
    "parent",
    "tracked_source_sha256",
    "pyproject_sha256",
    "uv_lock_sha256",
    "source_wheel",
    "environment",
}
_SOURCE_WHEEL_KEYS = {*_ARTIFACT_KEYS, "path", "direct_url"}
_RUN_KEYS = {
    "run_id",
    "target_id",
    "scenario_id",
    "argv",
    "expected_exit",
    "worker_exit",
    "expected_status",
    "observed_status",
    "certificate",
}

_TOP_LEVEL_INVARIANTS = (
    "execution_outcome",
    "state_transitions",
    "state_fork_isolation",
    "phase_contract",
    "tool_linkage",
    "exactly_once",
    "stream_parity",
)
_RECURSIVE_INVARIANTS = (
    "execution_outcome",
    "state_transitions",
    "state_fork_isolation",
    "recursive_approval_routing",
    "phase_contract",
    "exactly_once",
    "model_script_consumed",
    "stream_parity",
)
_SERIALIZED_RECURSIVE_INVARIANTS = (
    "execution_outcome",
    "state_transitions",
    "phase_contract",
    "exactly_once",
    "model_script_consumed",
    "stream_parity",
)
_SERIALIZED_RECURSIVE_FAILURE_FINGERPRINT = (
    ("execution_outcome", "FAIL", "UNEXPECTED_EXECUTION_OUTCOME"),
    ("state_transitions", "PASS", "OK"),
    ("phase_contract", "FAIL", "PHASE_OBSERVATION_MISMATCH"),
    ("exactly_once", "FAIL", "SIDE_EFFECT_COUNT_MISMATCH"),
    ("model_script_consumed", "FAIL", "UNCONSUMED_MODEL_STEPS"),
    ("stream_parity", "FAIL", "VARIANT_MISMATCH"),
)
_RECURSIVE_FAILURE_FINGERPRINT = (
    ("execution_outcome", "FAIL", "UNEXPECTED_EXECUTION_OUTCOME"),
    ("state_transitions", "PASS", "OK"),
    ("state_fork_isolation", "PASS", "OK"),
    (
        "recursive_approval_routing",
        "FAIL",
        "APPROVED_NESTED_STATE_REMAINED_INTERRUPTED",
    ),
    ("phase_contract", "FAIL", "PHASE_OBSERVATION_MISMATCH"),
    ("exactly_once", "FAIL", "SIDE_EFFECT_COUNT_MISMATCH"),
    ("model_script_consumed", "FAIL", "UNCONSUMED_MODEL_STEPS"),
    ("stream_parity", "PASS", "OK"),
)

_UPSTREAM_DEPENDENCY_ARTIFACTS: Final = {
    "annotated-types": (
        "0.8.0",
        "f072f4d804ea359e4eaf198b1af7a8b0943881a87f31bb764f8bf219bb9419e0",
    ),
    "anyio": ("4.14.2", "9f505dda5ac9f0c8309b5e8bd445a8c2bf7246f3ce950121e45ea15bc41d1494"),
    "attrs": ("26.1.0", "c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309"),
    "certifi": ("2026.7.22", "62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775"),
    "cffi": ("2.1.1", "c1453022f490d2459a11819d83ad1d586e9ff65a12ac3e705ffebd46d3685dcf"),
    "charset-normalizer": (
        "3.5.0",
        "14f6904a3cf870abf044df3a8c4924ac6c8ef77e9896586fd37e73ae96cff2af",
    ),
    "click": ("8.4.2", "e6f9f66136c816745b9d65817da91d61d957fb16e02e4dcd0552553c5a197b76"),
    "cryptography": ("50.0.0", "06a32a980526a6ab9a4b9bf8f7385800791e2bb960903cb6b530e4817509a3b7"),
    "distro": ("1.9.0", "7bffd925d65168f85027d8da9af6bddab658135b840670a223589bc0c8ef02b2"),
    "griffelib": ("2.0.1", "b769eed581c0e857d362fc8fcd8e57ecd2330c124b6104ac8b4c1c86d76970aa"),
    "h11": ("0.16.0", "63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86"),
    "httpcore2": ("2.10.0", "7df06cfb34070cae4f7c89be69dc1095eca138e9704ceffb98d25c1912ab6f01"),
    "httpx2": ("2.10.0", "5e3194a432701e1cc6f69a8b1b2fa199ef907013fede8d9a09a2c5b7b8141a18"),
    "idna": ("3.18", "7f952cbe720b688055e3f87de14f5c3e5fdaa8bc3928985c4077ca689de849a2"),
    "jiter": ("0.16.0", "46add52f4ad47a08bfb1219f3e673da972191489a33016edefdb5ea55bfa8c48"),
    "jsonschema": ("4.26.0", "d489f15263b8d200f8387e64b4c3a75f06629559fb73deb8fdfb525f2dab50ce"),
    "jsonschema-specifications": (
        "2025.9.1",
        "98802fee3a11ee76ecaca44429fda8a41bff98b00a0f2838151b113f210cc6fe",
    ),
    "mcp": ("2.0.0", "1cb4c75d2d2c7b8c1d756355e5d82a39f2822cc7f13e22a2051d7ca3592349d6"),
    "mcp-types": ("2.0.0", "6b2de797ca2797f568b79529e1b25948e34de511bcc0bd82fef1039a6d1b8eb0"),
    "openai": ("3.0.0", "8d32ac3a6647a66910d6cb8a64f0fa5a6c823604b6e82db83d9d055c6709bd51"),
    "opentelemetry-api": (
        "1.44.0",
        "94b98c893a91b88657eaac1e3ba89618cdb85be6918196705354f34728b2cdef",
    ),
    "pycparser": ("3.0", "b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992"),
    "pydantic": ("2.12.3", "6986454a854bc3bc6e5443e1369e06a3a456af9d339eda45510f517d9ea5c6bf"),
    "pydantic-core": ("2.41.4", "98f348cbb44fae6e9653c1055db7e29de67ea6a9ca03a5fa2c2e11a47cff0e47"),
    "pyjwt": ("2.13.0", "66adcc2aff09b3f1bbd95fc1e1577df8ac8723c978552fd43304c8a290ac5728"),
    "python-multipart": (
        "0.0.32",
        "ff6d3f776f16878c894e52e107296ffc890e913c611b1a4ec6c44e2821fe2e23",
    ),
    "referencing": ("0.37.0", "381329a9f99628c9069361716891d34ad94af76e461dcb0335825aecc7692231"),
    "requests": ("2.34.2", "2a0d60c172f83ac6ab31e4554906c0f3b3588d37b5cb939b1c061f4907e278e0"),
    "rpds-py": ("2026.6.3", "ecabd69db66de867690f9797f2f8fa27ba501bbc24540cbdbdc649cd15888ba6"),
    "sniffio": ("1.3.1", "2f6da418d1f1e0fddd844478f41680e794e6051915791a034ff65e5f100525a2"),
    "sse-starlette": ("3.4.8", "6e82314c786709a3cd9520f2285cf9fff90e181e598e8a357b0cf80f66afba0d"),
    "starlette": ("1.6.0", "a86dd39d14bb45f85a3d18525215a9ef0cfd1f192ac793220e72598c90335f0c"),
    "tqdm": ("4.70.0", "7f585706bfddbdebf89daac705b2dfcc16890130727d3197ca62c732b4310953"),
    "truststore": ("0.10.4", "adaeaecf1cbb5f4de3b1959b42d41f6fab57b2b1666adb59e89cb0b53361d981"),
    "typing-extensions": (
        "4.14.1",
        "d1e1e3b58374dc93031d6eda2420a48ea44a36c2b4766a4fdeb3710755731d76",
    ),
    "typing-inspection": (
        "0.4.2",
        "4ed1cacbdc298c220f1bd249ed5287caa16f34d44ef4e9c3d0cbad5b521545e7",
    ),
    "urllib3": ("2.7.0", "9fb4c81ebbb1ce9531cce37674bbc6f1360472bc18ca9a553ede278ef7276897"),
    "uvicorn": ("0.52.3", "116af2710dbf47c80f463cd20ee4884b6662f4c9f227d797ddc7279d2fcc2c7c"),
    "websockets": ("15.0.1", "64dee438fed052b52e4f98f76c5790513235efaa1ef7f3f2192c392cd7c91b65"),
}

_LIMITATIONS = [
    "This bundle is content-addressed and Git-anchored, not cryptographically signed.",
    "The socket-deny guard covers isolated worker execution, not artifact acquisition or wheel building.",
    "The AgentRunProof and source-built upstream wheels are hash-bound and published as CI or release artifacts, not committed beside this marker.",
    "This comparison covers only the named RunState scenarios and exact upstream source commits recorded in its targets.",
    "All SDK targets report version 0.20.0; target identity is established by wheel bytes and source provenance, never by version alone.",
]


class UpstreamComparisonError(ValueError):
    """Raised when an upstream comparison bundle is incomplete or inconsistent."""


class _RunSpec:
    __slots__ = (
        "run_id",
        "target_id",
        "scenario_id",
        "certificate_path",
        "expected_exit",
        "expected_status",
    )

    def __init__(
        self,
        run_id: str,
        target_id: str,
        scenario_id: str,
        certificate_path: str,
        expected_exit: int,
        expected_status: str,
    ) -> None:
        self.run_id = run_id
        self.target_id = target_id
        self.scenario_id = scenario_id
        self.certificate_path = certificate_path
        self.expected_exit = expected_exit
        self.expected_status = expected_status


RUN_SPECS: Final = (
    _RunSpec(
        "released-pypi-top-level",
        RELEASE_TARGET_ID,
        TOP_LEVEL_CASE_ID,
        RELEASED_CERTIFICATE_PATH,
        1,
        "FAIL",
    ),
    _RunSpec(
        "merged-source-top-level",
        UPSTREAM_TARGET_ID,
        TOP_LEVEL_CASE_ID,
        MERGED_TOP_LEVEL_CERTIFICATE_PATH,
        0,
        "PASS",
    ),
    _RunSpec(
        "merged-source-recursive-two-edge",
        UPSTREAM_TARGET_ID,
        RECURSIVE_CASE_ID,
        MERGED_RECURSIVE_CERTIFICATE_PATH,
        1,
        "FAIL",
    ),
)


def limitations() -> list[str]:
    return list(_LIMITATIONS)


def stable_worker_argv(scenario_id: str) -> list[str]:
    if not _IDENTIFIER.fullmatch(scenario_id):
        raise UpstreamComparisonError(f"Invalid comparison scenario: {scenario_id!r}.")
    return [
        "python",
        "-I",
        "-m",
        "agentrunproof.current.worker",
        scenario_id,
    ]


def source_wheel_member_path(commit: str) -> str:
    """Return the collision-free evidence name for a source-built SDK wheel."""

    if not _GIT_OBJECT.fullmatch(commit):
        raise UpstreamComparisonError("commit must be a 40-character Git object ID.")
    return f"openai_agents-0.20.0-from-{commit[:12]}.whl"


def finalize_comparison_certificate(
    value: Any,
    *,
    source_commit: str,
) -> dict[str, JsonValue]:
    """Bind one installed-wheel certificate to the clean harness source commit."""

    if not _GIT_OBJECT.fullmatch(source_commit):
        raise UpstreamComparisonError("source_commit must be a 40-character Git object ID.")
    try:
        certificate = validate_certificate(value)
    except CertificateError as error:
        raise UpstreamComparisonError(f"Invalid worker certificate: {error}") from error
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
    try:
        return validate_certificate(candidate)
    except CertificateError as error:
        raise UpstreamComparisonError(f"Cannot bind worker certificate: {error}") from error


def parse_worker_certificate_json(text: str) -> dict[str, JsonValue]:
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except UpstreamComparisonError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise UpstreamComparisonError(f"Cannot parse worker certificate: {error}") from error
    try:
        return validate_certificate(payload)
    except CertificateError as error:
        raise UpstreamComparisonError(f"Invalid worker certificate: {error}") from error


def finalize_upstream_comparison(
    value: Any,
    *,
    directory: Path,
) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise UpstreamComparisonError("The upstream comparison must be a JSON object.")
    candidate = copy.deepcopy(value)
    if "bundle_id" not in candidate:
        candidate["bundle_id"] = None
    elif candidate["bundle_id"] is not None:
        raise UpstreamComparisonError("A comparison being finalized must have a null bundle_id.")
    candidate["bundle_id"] = _content_id(candidate, "bundle_id")
    return validate_upstream_comparison(candidate, directory=directory)


def validate_upstream_comparison(
    value: Any,
    *,
    directory: Path,
) -> dict[str, JsonValue]:
    bundle = _object(value, "upstream comparison")
    _exact_keys(bundle, _BUNDLE_KEYS, "upstream comparison")
    _expect(bundle.get("schema_version"), COMPARISON_SCHEMA_VERSION, "schema_version")
    identifier = bundle.get("bundle_id")
    if not isinstance(identifier, str) or not _PREFIXED_SHA256.fullmatch(identifier):
        raise UpstreamComparisonError("bundle_id must be a lowercase SHA-256 identifier.")
    if identifier != _content_id(bundle, "bundle_id"):
        raise UpstreamComparisonError("bundle_id does not match the canonical bundle payload.")

    source = _validate_source(bundle.get("source"))
    runtime_python = _validate_runtime(bundle.get("runtime"))
    harness = _object(bundle.get("harness"), "harness")
    _exact_keys(harness, _HARNESS_KEYS, "harness")
    harness_wheel = _validate_file_member(
        harness.get("wheel"), label="harness.wheel", expected_path=None
    )
    harness_wheel_name = cast(str, harness_wheel["path"])
    match = _HARNESS_WHEEL.fullmatch(harness_wheel_name)
    if match is None:
        raise UpstreamComparisonError("harness.wheel.path is not a canonical wheel name.")
    _validate_optional_member(
        directory / harness_wheel_name,
        harness_wheel,
        label="harness wheel",
    )

    targets = bundle.get("targets")
    if not isinstance(targets, list) or len(targets) < 2:
        raise UpstreamComparisonError(
            "targets must contain the released target and at least one source target."
        )
    released = _validate_release_target(targets[0], runtime_python=runtime_python)
    validated_targets = [released]
    for index, raw_target in enumerate(targets[1:], start=1):
        validated_targets.append(
            _validate_upstream_target(
                raw_target,
                index=index,
                runtime_python=runtime_python,
                directory=directory,
            )
        )
    target_ids = [cast(str, target["target_id"]) for target in validated_targets]
    if target_ids[1] != UPSTREAM_TARGET_ID:
        raise UpstreamComparisonError("The audited #4413 source target must be targets[1].")
    if target_ids[2:] != sorted(target_ids[2:]):
        raise UpstreamComparisonError("Additional source targets must be ordered by target_id.")
    if len(target_ids) != len(set(target_ids)):
        raise UpstreamComparisonError("Every target_id must be unique.")

    release_wheel = _object(released.get("wheel"), "released target wheel")
    wheel_hashes = [cast(str, release_wheel["sha256"])]
    source_member_paths: list[str] = []
    source_commits: list[str] = []
    for target in validated_targets[1:]:
        source_wheel = _object(target.get("source_wheel"), "upstream source wheel")
        _expect(released.get("version"), target.get("version"), "target versions")
        wheel_hashes.append(cast(str, source_wheel["sha256"]))
        source_member_paths.append(cast(str, source_wheel["path"]))
        source_commits.append(cast(str, target["commit"]))
    if len(wheel_hashes) != len(set(wheel_hashes)):
        raise UpstreamComparisonError("Every SDK target must bind different wheel bytes.")
    if len(source_member_paths) != len(set(source_member_paths)):
        raise UpstreamComparisonError("Every source-built wheel member path must be unique.")
    if len(source_commits) != len(set(source_commits)):
        raise UpstreamComparisonError("Every source target must bind a distinct commit.")

    runs = bundle.get("runs")
    if not isinstance(runs, list) or len(runs) < len(RUN_SPECS):
        raise UpstreamComparisonError(
            "runs must contain the three audited #4413 observations, followed by any additions."
        )
    run_specs: list[_RunSpec] = list(RUN_SPECS)
    target_id_set = set(target_ids)
    for index, raw_run in enumerate(runs[len(RUN_SPECS) :], start=len(RUN_SPECS)):
        run_specs.append(_additional_run_spec(raw_run, index=index, target_ids=target_id_set))
    _validate_run_order(run_specs, target_ids=target_ids)

    certificate_ids: set[str] = set()
    certificate_paths: set[str] = set()
    environments = {
        cast(str, target["target_id"]): cast(dict[str, JsonValue], target["environment"])
        for target in validated_targets
    }
    for index, (raw_run, spec) in enumerate(zip(runs, run_specs, strict=True)):
        certificate = _validate_run(
            raw_run,
            spec=spec,
            directory=directory,
            source=source,
            harness_version=match.group("version"),
            environment=environments[spec.target_id],
        )
        certificate_id = certificate.get("certificate_id")
        assert isinstance(certificate_id, str)
        if certificate_id in certificate_ids:
            raise UpstreamComparisonError("Every run must bind a distinct certificate.")
        certificate_ids.add(certificate_id)
        if spec.certificate_path in certificate_paths:
            raise UpstreamComparisonError("Every run must use a distinct certificate path.")
        certificate_paths.add(spec.certificate_path)
        _validate_run_semantics(certificate, spec=spec, baseline_index=index)

    if {spec.target_id for spec in run_specs} != target_id_set:
        raise UpstreamComparisonError("Every target must be referenced by at least one run.")
    all_member_paths = [harness_wheel_name, *source_member_paths, *certificate_paths]
    if len(all_member_paths) != len(set(all_member_paths)):
        raise UpstreamComparisonError("Bundle member paths must be unique.")

    _expect(bundle.get("limitations"), _LIMITATIONS, "limitations")
    return cast(dict[str, JsonValue], copy.deepcopy(bundle))


def load_upstream_comparison(path: Path) -> dict[str, JsonValue]:
    if path.is_symlink() or not path.is_file():
        raise UpstreamComparisonError("The comparison bundle must be a regular file.")
    try:
        data = path.read_bytes()
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except UpstreamComparisonError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise UpstreamComparisonError(f"Cannot read upstream comparison: {error}") from error
    return validate_upstream_comparison(payload, directory=path.parent)


def upstream_comparison_json(value: Any, *, directory: Path) -> str:
    bundle = validate_upstream_comparison(value, directory=directory)
    return json.dumps(bundle, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_upstream_comparison(path: Path, value: Any) -> None:
    rendered = upstream_comparison_json(value, directory=path.parent)
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
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validate_source(value: Any) -> dict[str, JsonValue]:
    source = _object(value, "source")
    _exact_keys(source, _SOURCE_KEYS, "source")
    for key in ("commit", "tree"):
        item = source.get(key)
        if not isinstance(item, str) or not _GIT_OBJECT.fullmatch(item):
            raise UpstreamComparisonError(f"source.{key} must be a 40-character Git object ID.")
    if source.get("clean") is not True or source.get("index_flags_clean") is not True:
        raise UpstreamComparisonError("Canonical comparison requires clean harness provenance.")
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
    for key, expected_value in expected.items():
        _expect(runtime.get(key), expected_value, f"runtime.{key}")
    return "3.12.13"


def _validate_release_target(value: Any, *, runtime_python: str) -> dict[str, JsonValue]:
    target = _object(value, "targets[0]")
    _exact_keys(target, _RELEASE_TARGET_KEYS, "targets[0]")
    expected: dict[str, JsonValue] = {
        "target_id": RELEASE_TARGET_ID,
        "kind": "pypi-wheel",
        "distribution": "openai-agents",
        "version": "0.20.0",
    }
    for key, expected_value in expected.items():
        _expect(target.get(key), expected_value, f"targets[0].{key}")
    wheel = _validate_artifact(target.get("wheel"), label="targets[0].wheel")
    _expect(wheel.get("distribution"), "openai-agents", "targets[0].wheel.distribution")
    _expect(wheel.get("version"), "0.20.0", "targets[0].wheel.version")
    _expect(wheel.get("sha256"), RELEASE_WHEEL_SHA256, "targets[0].wheel.sha256")
    if wheel.get("filename") != "openai_agents-0.20.0-py3-none-any.whl":
        raise UpstreamComparisonError("targets[0].wheel.filename is not the PyPI 0.20.0 wheel.")
    environment = _validated_environment(target.get("environment"), label="targets[0].environment")
    _expect(environment.get("environment_id"), RELEASE_ENVIRONMENT_ID, "released environment")
    _expect(environment.get("python"), runtime_python, "released environment.python")
    _expect(_sdk_artifact(environment), wheel, "released environment SDK artifact")
    result = copy.deepcopy(target)
    result["wheel"] = wheel
    result["environment"] = environment
    return cast(dict[str, JsonValue], result)


def _validate_upstream_target(
    value: Any,
    *,
    index: int,
    runtime_python: str,
    directory: Path,
) -> dict[str, JsonValue]:
    label = f"targets[{index}]"
    target = _object(value, label)
    _exact_keys(target, _UPSTREAM_TARGET_KEYS, label)
    expected: dict[str, JsonValue] = {
        "kind": "source-built-wheel",
        "distribution": "openai-agents",
        "version": "0.20.0",
        "repository": UPSTREAM_REPOSITORY,
    }
    for key, expected_value in expected.items():
        _expect(target.get(key), expected_value, f"{label}.{key}")
    target_id = target.get("target_id")
    if not isinstance(target_id, str) or not _IDENTIFIER.fullmatch(target_id):
        raise UpstreamComparisonError(f"{label}.target_id is invalid.")
    for key in ("commit", "tree", "parent"):
        item = target.get(key)
        if not isinstance(item, str) or not _GIT_OBJECT.fullmatch(item):
            raise UpstreamComparisonError(f"{label}.{key} must be a Git object ID.")
    commit = cast(str, target["commit"])
    if target_id != UPSTREAM_TARGET_ID and target_id != f"openai-agents-python-{commit[:8]}":
        raise UpstreamComparisonError(f"{label}.target_id must identify its source commit.")
    for key in ("tracked_source_sha256", "pyproject_sha256", "uv_lock_sha256"):
        item = target.get(key)
        if not isinstance(item, str) or not _SHA256.fullmatch(item):
            raise UpstreamComparisonError(f"{label}.{key} must be a lowercase SHA-256.")
    _expect(
        target.get("pyproject_sha256"),
        UPSTREAM_PYPROJECT_SHA256,
        f"{label}.pyproject_sha256",
    )
    _expect(
        target.get("uv_lock_sha256"),
        UPSTREAM_UV_LOCK_SHA256,
        f"{label}.uv_lock_sha256",
    )
    if target_id == UPSTREAM_TARGET_ID:
        pinned: dict[str, JsonValue] = {
            "commit": UPSTREAM_COMMIT,
            "tree": UPSTREAM_TREE,
            "parent": UPSTREAM_PARENT,
            "tracked_source_sha256": UPSTREAM_TRACKED_SOURCE_SHA256,
            "pyproject_sha256": UPSTREAM_PYPROJECT_SHA256,
            "uv_lock_sha256": UPSTREAM_UV_LOCK_SHA256,
        }
        for key, expected_value in pinned.items():
            _expect(target.get(key), expected_value, f"{label}.{key}")

    source_wheel_value = _object(target.get("source_wheel"), f"{label}.source_wheel")
    _exact_keys(source_wheel_value, _SOURCE_WHEEL_KEYS, f"{label}.source_wheel")
    artifact_payload = {
        key: source_wheel_value[key] for key in _ARTIFACT_KEYS if key in source_wheel_value
    }
    source_wheel = _validate_artifact(artifact_payload, label=f"{label}.source_wheel")
    _expect(
        source_wheel.get("distribution"),
        "openai-agents",
        f"{label}.source_wheel.distribution",
    )
    _expect(source_wheel.get("version"), "0.20.0", f"{label}.source_wheel.version")
    filename = cast(str, source_wheel["filename"])
    if _SDK_WHEEL.fullmatch(filename) is None:
        raise UpstreamComparisonError(f"{label}.source_wheel.filename is not canonical.")
    member_path = source_wheel_value.get("path")
    expected_member_path = source_wheel_member_path(commit)
    if member_path != expected_member_path:
        raise UpstreamComparisonError(
            f"{label}.source_wheel.path must be {expected_member_path!r}."
        )
    direct_url = _object(source_wheel_value.get("direct_url"), f"{label}.source_wheel.direct_url")
    _exact_keys(direct_url, _DIRECT_URL_KEYS, f"{label}.source_wheel.direct_url")
    _expect(
        direct_url.get("url_scheme"),
        "file",
        f"{label}.source_wheel.direct_url.url_scheme",
    )
    _expect(
        direct_url.get("url_basename"),
        filename,
        f"{label}.source_wheel.direct_url.url_basename",
    )
    _expect(
        direct_url.get("archive_sha256"),
        source_wheel.get("sha256"),
        f"{label}.source_wheel.direct_url.archive_sha256",
    )
    _validate_optional_member(
        directory / cast(str, member_path),
        {
            "path": cast(str, member_path),
            "size": source_wheel["size"],
            "sha256": source_wheel["sha256"],
        },
        label=f"{label} source-built upstream wheel",
    )
    environment = _validated_environment(target.get("environment"), label=f"{label}.environment")
    _expect(environment.get("python"), runtime_python, f"{label}.environment.python")
    _expect(environment.get("lock_sha256"), UPSTREAM_LOCK_SHA256, f"{label}.environment.lock")
    if environment.get("environment_id") == RELEASE_ENVIRONMENT_ID:
        raise UpstreamComparisonError("The upstream target must use a separate closure.")
    _validate_upstream_closure(environment, source_wheel=source_wheel)
    _expect(_sdk_artifact(environment), source_wheel, f"{label}.environment SDK artifact")
    result = copy.deepcopy(target)
    result["source_wheel"] = {
        **source_wheel,
        "path": cast(str, member_path),
        "direct_url": copy.deepcopy(direct_url),
    }
    result["environment"] = environment
    return cast(dict[str, JsonValue], result)


def _additional_run_spec(
    value: Any,
    *,
    index: int,
    target_ids: set[str],
) -> _RunSpec:
    label = f"runs[{index}]"
    run = _object(value, label)
    _exact_keys(run, _RUN_KEYS, label)
    run_id = run.get("run_id")
    target_id = run.get("target_id")
    scenario_id = run.get("scenario_id")
    if not isinstance(run_id, str) or not _IDENTIFIER.fullmatch(run_id):
        raise UpstreamComparisonError(f"{label}.run_id is invalid.")
    if not isinstance(target_id, str) or target_id not in target_ids:
        raise UpstreamComparisonError(f"{label}.target_id does not name a bundle target.")
    if not isinstance(scenario_id, str):
        raise UpstreamComparisonError(f"{label}.scenario_id is invalid.")
    stable_worker_argv(scenario_id)
    status = run.get("expected_status")
    if not isinstance(status, str) or status not in {"PASS", "FAIL"}:
        raise UpstreamComparisonError(f"{label}.expected_status must be PASS or FAIL.")
    expected_exit = 0 if status == "PASS" else 1
    _expect(run.get("expected_exit"), expected_exit, f"{label}.expected_exit")
    _expect(run.get("worker_exit"), expected_exit, f"{label}.worker_exit")
    _expect(run.get("observed_status"), status, f"{label}.observed_status")
    member = _object(run.get("certificate"), f"{label}.certificate")
    certificate_path = member.get("path")
    if not isinstance(certificate_path, str):
        raise UpstreamComparisonError(f"{label}.certificate.path is invalid.")
    return _RunSpec(
        run_id,
        target_id,
        scenario_id,
        certificate_path,
        expected_exit,
        status,
    )


def _validate_run_order(specs: list[_RunSpec], *, target_ids: list[str]) -> None:
    run_ids = [spec.run_id for spec in specs]
    if len(run_ids) != len(set(run_ids)):
        raise UpstreamComparisonError("Every run_id must be unique.")
    pairs = [(spec.target_id, spec.scenario_id) for spec in specs]
    if len(pairs) != len(set(pairs)):
        raise UpstreamComparisonError("Every target/scenario pair must be observed once.")
    target_order = {target_id: index for index, target_id in enumerate(target_ids)}
    indexes = [target_order[spec.target_id] for spec in specs]
    if indexes != sorted(indexes):
        raise UpstreamComparisonError("Runs must be grouped in target order.")


def _validate_run_semantics(
    certificate: dict[str, JsonValue],
    *,
    spec: _RunSpec,
    baseline_index: int,
) -> None:
    if baseline_index == 0:
        try:
            validate_current_certificate(certificate)
        except ValueError as error:
            raise UpstreamComparisonError(
                f"Released top-level certificate has the wrong fingerprint: {error}"
            ) from error
        return
    if baseline_index == 2:
        _validate_recursive_failure_certificate(certificate)
        return
    if spec.scenario_id == TOP_LEVEL_CASE_ID:
        if spec.expected_status != "PASS":
            raise UpstreamComparisonError(
                "Only the audited released target may carry a failing top-level observation."
            )
        _validate_fixed_top_level_certificate(certificate)
        return
    if spec.scenario_id == SERIALIZED_RECURSIVE_CASE_ID:
        _validate_serialized_recursive_certificate(
            certificate,
            expected_status=spec.expected_status,
        )
        return
    scenario = _object(certificate.get("scenario"), "recursive scenario")
    if scenario.get("requested_invariants") != list(_RECURSIVE_INVARIANTS):
        raise UpstreamComparisonError(
            "Additional scenarios must use the audited recursive-approval invariant contract."
        )
    if spec.expected_status == "PASS":
        _validate_recursive_success_certificate(certificate)
    else:
        _validate_recursive_failure_certificate(certificate)


def _validate_run(
    value: Any,
    *,
    spec: _RunSpec,
    directory: Path,
    source: dict[str, JsonValue],
    harness_version: str,
    environment: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    run = _object(value, f"run {spec.run_id}")
    _exact_keys(run, _RUN_KEYS, f"run {spec.run_id}")
    expected: dict[str, JsonValue] = {
        "run_id": spec.run_id,
        "target_id": spec.target_id,
        "scenario_id": spec.scenario_id,
        "argv": cast(JsonValue, stable_worker_argv(spec.scenario_id)),
        "expected_exit": spec.expected_exit,
        "worker_exit": spec.expected_exit,
        "expected_status": spec.expected_status,
        "observed_status": spec.expected_status,
    }
    for key, expected_value in expected.items():
        _expect(run.get(key), expected_value, f"run {spec.run_id}.{key}")
    member = _validate_file_member(
        run.get("certificate"),
        label=f"run {spec.run_id}.certificate",
        expected_path=spec.certificate_path,
        keys=_CERTIFICATE_MEMBER_KEYS,
    )
    certificate_bytes = _required_member_bytes(
        directory / spec.certificate_path,
        label=f"run {spec.run_id} certificate",
    )
    _expect(len(certificate_bytes), member["size"], f"run {spec.run_id}.certificate.size")
    _expect(
        hashlib.sha256(certificate_bytes).hexdigest(),
        member["sha256"],
        f"run {spec.run_id}.certificate.sha256",
    )
    try:
        certificate = parse_worker_certificate_json(certificate_bytes.decode("utf-8"))
    except (UnicodeError, UpstreamComparisonError) as error:
        raise UpstreamComparisonError(
            f"Run {spec.run_id} certificate member is invalid: {error}"
        ) from error
    _expect(
        certificate.get("certificate_id"),
        member.get("certificate_id"),
        f"run {spec.run_id}.certificate.certificate_id",
    )
    _expect(
        certificate.get("overall_status"),
        spec.expected_status,
        f"run {spec.run_id}.certificate.overall_status",
    )
    certificate_source = _object(certificate.get("source"), "certificate.source")
    _expect(
        certificate_source,
        {
            "commit": source.get("commit"),
            "dirty": False,
            "tracked_diff_sha256": sha256_hex(""),
            "untracked_paths_sha256": sha256_hex([]),
            "index_flags_sha256": sha256_hex([]),
        },
        f"run {spec.run_id}.certificate.source",
    )
    tool = _object(certificate.get("tool"), "certificate.tool")
    _expect(tool.get("name"), "agentrunproof", "certificate.tool.name")
    _expect(tool.get("version"), harness_version, "certificate.tool.version")
    scenario = _object(certificate.get("scenario"), "certificate.scenario")
    _expect(scenario.get("id"), spec.scenario_id, "certificate.scenario.id")
    _expect(scenario.get("revision"), 1, "certificate.scenario.revision")
    _validate_certificate_environment(certificate, environment)
    return certificate


def _validate_fixed_top_level_certificate(certificate: dict[str, JsonValue]) -> None:
    _expect(certificate.get("overall_status"), "PASS", "merged top-level overall_status")
    scenario = _object(certificate.get("scenario"), "merged top-level scenario")
    _expect(
        scenario.get("requested_invariants"),
        list(_TOP_LEVEL_INVARIANTS),
        "merged top-level requested_invariants",
    )
    expected = tuple((name, "PASS", "OK") for name in _TOP_LEVEL_INVARIANTS)
    _expect(
        _invariant_fingerprint(certificate),
        expected,
        "merged top-level invariant fingerprint",
    )
    observations = _variant_observations(certificate)
    for variant, observation in observations.items():
        label = f"merged top-level {variant}"
        _expect(observation.get("final_output"), None, f"{label}.final_output")
        _expect(observation.get("interruption_count"), 1, f"{label}.interruption_count")
        _expect(observation.get("tool_counts"), {"approval_tool": 0}, f"{label}.tool_counts")
        _expect(observation.get("remaining_model_steps"), 1, f"{label}.remaining_model_steps")
        _expect(observation.get("exception"), None, f"{label}.exception")
        phases = observation.get("phases")
        if not isinstance(phases, list) or len(phases) != 2:
            raise UpstreamComparisonError(f"{label}.phases must contain two phases.")
        initial = _object(phases[0], f"{label}.phases[0]")
        fork = _object(phases[1], f"{label}.phases[1]")
        _expect(initial.get("phase_id"), "initial", f"{label}.phases[0].phase_id")
        _expect(initial.get("interruption_count"), 1, f"{label}.initial interruption")
        _expect(fork.get("phase_id"), "fork-check", f"{label}.phases[1].phase_id")
        _expect(fork.get("interruption_count"), 1, f"{label}.fork interruption")
        _expect(fork.get("tool_counts_delta"), {"approval_tool": 0}, f"{label}.fork effects")
        transition = _object(fork.get("state_transition"), f"{label}.fork transition")
        _expect(transition.get("subject_state_unchanged"), True, f"{label}.state isolation")


def _validate_recursive_failure_certificate(certificate: dict[str, JsonValue]) -> None:
    _expect(certificate.get("overall_status"), "FAIL", "merged recursive overall_status")
    scenario = _object(certificate.get("scenario"), "merged recursive scenario")
    _expect(
        scenario.get("requested_invariants"),
        list(_RECURSIVE_INVARIANTS),
        "merged recursive requested_invariants",
    )
    _expect(
        _invariant_fingerprint(certificate),
        _RECURSIVE_FAILURE_FINGERPRINT,
        "merged recursive invariant fingerprint",
    )
    observations = _variant_observations(certificate)
    for variant, observation in observations.items():
        label = f"merged recursive {variant}"
        _expect(observation.get("final_output"), None, f"{label}.final_output")
        _expect(observation.get("interruption_count"), 1, f"{label}.interruption_count")
        _expect(
            observation.get("tool_counts"),
            {"protected_effect": 0},
            f"{label}.tool_counts",
        )
        _expect(observation.get("remaining_model_steps"), 1, f"{label}.remaining_model_steps")
        _expect(observation.get("exception"), None, f"{label}.exception")
        phases = observation.get("phases")
        if not isinstance(phases, list) or len(phases) != 3:
            raise UpstreamComparisonError(f"{label}.phases must contain three phases.")
        initial, untouched, approved = (
            _object(phases[0], f"{label}.phases[0]"),
            _object(phases[1], f"{label}.phases[1]"),
            _object(phases[2], f"{label}.phases[2]"),
        )
        _expect(initial.get("phase_id"), "initial", f"{label}.initial phase")
        _expect(initial.get("interruption_count"), 1, f"{label}.initial interruption")
        _expect(untouched.get("phase_id"), "untouched-sibling", f"{label}.untouched phase")
        _expect(untouched.get("interruption_count"), 1, f"{label}.untouched interruption")
        _expect(
            untouched.get("tool_counts_delta"),
            {"protected_effect": 0},
            f"{label}.untouched effects",
        )
        untouched_transition = _object(
            untouched.get("state_transition"), f"{label}.untouched transition"
        )
        _expect(untouched_transition.get("subject_state_unchanged"), True, f"{label}.isolation")
        _expect(
            untouched_transition.get("sibling_state_saved"),
            True,
            f"{label}.saved branch",
        )
        _expect(approved.get("phase_id"), "approved-sibling", f"{label}.approved phase")
        _expect(approved.get("interruption_count"), 1, f"{label}.approved interruption")
        _expect(
            approved.get("tool_counts_delta"),
            {"protected_effect": 0},
            f"{label}.approved effects",
        )
        approved_transition = _object(
            approved.get("state_transition"), f"{label}.approved transition"
        )
        _expect(
            approved_transition.get("saved_sibling_from"),
            "untouched-sibling",
            f"{label}.approved branch source",
        )
        _expect(
            approved_transition.get("saved_sibling_state_sha256"),
            untouched_transition.get("saved_sibling_state_sha256"),
            f"{label}.saved branch digest",
        )


def _validate_recursive_success_certificate(certificate: dict[str, JsonValue]) -> None:
    _expect(certificate.get("overall_status"), "PASS", "fixed recursive overall_status")
    scenario = _object(certificate.get("scenario"), "fixed recursive scenario")
    _expect(
        scenario.get("requested_invariants"),
        list(_RECURSIVE_INVARIANTS),
        "fixed recursive requested_invariants",
    )
    expected = tuple((name, "PASS", "OK") for name in _RECURSIVE_INVARIANTS)
    _expect(
        _invariant_fingerprint(certificate),
        expected,
        "fixed recursive invariant fingerprint",
    )
    observations = _variant_observations(certificate)
    for variant, observation in observations.items():
        label = f"fixed recursive {variant}"
        _expect(observation.get("final_output"), "outer complete", f"{label}.final_output")
        _expect(observation.get("interruption_count"), 0, f"{label}.interruption_count")
        _expect(
            observation.get("tool_counts"),
            {"protected_effect": 1},
            f"{label}.tool_counts",
        )
        _expect(observation.get("remaining_model_steps"), 0, f"{label}.remaining_model_steps")
        _expect(observation.get("exception"), None, f"{label}.exception")
        phases = observation.get("phases")
        if not isinstance(phases, list) or len(phases) != 3:
            raise UpstreamComparisonError(f"{label}.phases must contain three phases.")
        initial, untouched, approved = (
            _object(phases[0], f"{label}.phases[0]"),
            _object(phases[1], f"{label}.phases[1]"),
            _object(phases[2], f"{label}.phases[2]"),
        )
        _expect(initial.get("phase_id"), "initial", f"{label}.initial phase")
        _expect(initial.get("interruption_count"), 1, f"{label}.initial interruption")
        _expect(untouched.get("phase_id"), "untouched-sibling", f"{label}.untouched phase")
        _expect(untouched.get("interruption_count"), 1, f"{label}.untouched interruption")
        _expect(
            untouched.get("tool_counts_delta"),
            {"protected_effect": 0},
            f"{label}.untouched effects",
        )
        untouched_transition = _object(
            untouched.get("state_transition"), f"{label}.untouched transition"
        )
        _expect(untouched_transition.get("subject_state_unchanged"), True, f"{label}.isolation")
        _expect(untouched_transition.get("sibling_state_saved"), True, f"{label}.saved branch")
        _expect(approved.get("phase_id"), "approved-sibling", f"{label}.approved phase")
        _expect(approved.get("final_output"), "outer complete", f"{label}.approved output")
        _expect(approved.get("interruption_count"), 0, f"{label}.approved interruption")
        _expect(
            approved.get("tool_counts_delta"),
            {"protected_effect": 1},
            f"{label}.approved effects",
        )
        _expect(
            approved.get("probes_after", {}).get("protected_effects")
            if isinstance(approved.get("probes_after"), dict)
            else None,
            ["committed-once"],
            f"{label}.approved protected effects",
        )
        approved_transition = _object(
            approved.get("state_transition"), f"{label}.approved transition"
        )
        _expect(
            approved_transition.get("saved_sibling_from"),
            "untouched-sibling",
            f"{label}.approved branch source",
        )
        _expect(
            approved_transition.get("saved_sibling_state_sha256"),
            untouched_transition.get("saved_sibling_state_sha256"),
            f"{label}.saved branch digest",
        )


def _validate_serialized_recursive_certificate(
    certificate: dict[str, JsonValue],
    *,
    expected_status: str,
) -> None:
    _expect(
        certificate.get("overall_status"),
        expected_status,
        "serialized recursive overall_status",
    )
    scenario = _object(certificate.get("scenario"), "serialized recursive scenario")
    _expect(
        scenario.get("requested_invariants"),
        list(_SERIALIZED_RECURSIVE_INVARIANTS),
        "serialized recursive requested_invariants",
    )
    fingerprint = _invariant_fingerprint(certificate)
    if expected_status == "PASS":
        _expect(
            fingerprint,
            tuple((name, "PASS", "OK") for name in _SERIALIZED_RECURSIVE_INVARIANTS),
            "fixed serialized recursive invariant fingerprint",
        )
    else:
        expected_prefix = _SERIALIZED_RECURSIVE_FAILURE_FINGERPRINT[:-1]
        _expect(
            fingerprint[:-1],
            expected_prefix,
            "failing serialized recursive invariant fingerprint",
        )
        if fingerprint[-1] not in {
            ("stream_parity", "PASS", "OK"),
            ("stream_parity", "FAIL", "VARIANT_MISMATCH"),
        }:
            raise UpstreamComparisonError(
                "failing serialized recursive stream-parity fingerprint is invalid."
            )

    observations = _variant_observations(certificate)
    for variant, observation in observations.items():
        label = f"serialized recursive {variant}"
        if expected_status == "PASS":
            _expect(observation.get("final_output"), "outer complete", f"{label}.final_output")
            _expect(observation.get("interruption_count"), 0, f"{label}.interruption_count")
            _expect(
                observation.get("tool_counts"),
                {"protected_effect": 1},
                f"{label}.tool_counts",
            )
            _expect(observation.get("remaining_model_steps"), 0, f"{label}.remaining_model_steps")
        else:
            _expect(observation.get("final_output"), None, f"{label}.final_output")
            _expect(observation.get("interruption_count"), 1, f"{label}.interruption_count")
            _expect(
                observation.get("tool_counts"),
                {"protected_effect": 0},
                f"{label}.tool_counts",
            )
            _expect(observation.get("remaining_model_steps"), 1, f"{label}.remaining_model_steps")
        _expect(observation.get("exception"), None, f"{label}.exception")
        phases = observation.get("phases")
        if not isinstance(phases, list) or len(phases) != 2:
            raise UpstreamComparisonError(f"{label}.phases must contain two phases.")
        initial = _object(phases[0], f"{label}.phases[0]")
        approved = _object(phases[1], f"{label}.phases[1]")
        _expect(initial.get("phase_id"), "initial", f"{label}.initial phase")
        _expect(initial.get("interruption_count"), 1, f"{label}.initial interruption")
        _expect(approved.get("phase_id"), "serialized-approved", f"{label}.approved phase")
        expected_interruption = 0 if expected_status == "PASS" else 1
        expected_delta = {"protected_effect": 1 if expected_status == "PASS" else 0}
        _expect(
            approved.get("interruption_count"),
            expected_interruption,
            f"{label}.approved interruption",
        )
        _expect(approved.get("tool_counts_delta"), expected_delta, f"{label}.approved effects")
        transition = _object(approved.get("state_transition"), f"{label}.approved transition")
        for key in (
            "json_round_trip_requested",
            "json_round_trip_equal",
            "restored_state_equal",
        ):
            _expect(transition.get(key), True, f"{label}.{key}")
        state_schema_version = transition.get("state_schema_version")
        if not isinstance(state_schema_version, str) or not state_schema_version:
            raise UpstreamComparisonError(f"{label}.state_schema_version is missing.")
        decisions = transition.get("decisions")
        if not isinstance(decisions, list) or len(decisions) != 1:
            raise UpstreamComparisonError(f"{label}.decisions must contain one approval.")
        _expect(
            decisions[0],
            {
                "action": "approve",
                "call_id_sha256": (
                    "18a3037806c47a97bbcc4dda440b96c9213b8ff2114345156c2bde1e44f226ec"
                ),
                "matched": True,
            },
            f"{label}.approval decision",
        )


def _variant_observations(certificate: dict[str, JsonValue]) -> dict[str, dict[str, Any]]:
    observations = _object(certificate.get("observations"), "certificate.observations")
    _exact_keys(observations, {"non_streaming", "streaming"}, "certificate.observations")
    return {
        variant: _object(observations[variant], f"certificate.observations.{variant}")
        for variant in ("non_streaming", "streaming")
    }


def _invariant_fingerprint(certificate: dict[str, JsonValue]) -> tuple[tuple[Any, Any, Any], ...]:
    invariants = certificate.get("invariants")
    if not isinstance(invariants, list):
        raise UpstreamComparisonError("certificate.invariants must be an array.")
    return tuple(
        (
            _object(item, f"certificate.invariants[{index}]").get("name"),
            _object(item, f"certificate.invariants[{index}]").get("status"),
            _object(item, f"certificate.invariants[{index}]").get("reason"),
        )
        for index, item in enumerate(invariants)
    )


def _validate_certificate_environment(
    certificate: dict[str, JsonValue], environment: dict[str, JsonValue]
) -> None:
    runtime = _object(certificate.get("runtime"), "certificate.runtime")
    _expect(runtime.get("python"), "3.12.13", "certificate.runtime.python")
    _expect(runtime.get("implementation"), "CPython", "certificate.runtime.implementation")
    _expect(
        runtime.get("platform"),
        {"system": "Linux", "machine": "x86_64"},
        "certificate.runtime.platform",
    )
    packages = _object(runtime.get("packages"), "certificate.runtime.packages")
    versions: dict[str, Any] = {}
    artifacts = environment.get("artifacts")
    assert isinstance(artifacts, list)
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


def _validated_environment(value: Any, *, label: str) -> dict[str, JsonValue]:
    try:
        environment = validate_environment(value)
    except ValueError as error:
        raise UpstreamComparisonError(f"Invalid {label}: {error}") from error
    _expect(environment.get("sdk_version"), "0.20.0", f"{label}.sdk_version")
    return environment


def _validate_upstream_closure(
    environment: dict[str, JsonValue],
    *,
    source_wheel: dict[str, JsonValue],
) -> None:
    artifacts = environment.get("artifacts")
    if not isinstance(artifacts, list):
        raise UpstreamComparisonError("upstream environment.artifacts must be an array.")
    actual: dict[str, tuple[Any, Any]] = {}
    for raw_artifact in artifacts:
        artifact = _object(raw_artifact, "upstream environment artifact")
        distribution = artifact.get("distribution")
        if isinstance(distribution, str):
            actual[distribution] = (artifact.get("version"), artifact.get("sha256"))
    expected: dict[str, tuple[Any, Any]] = dict(_UPSTREAM_DEPENDENCY_ARTIFACTS)
    expected["openai-agents"] = (source_wheel.get("version"), source_wheel.get("sha256"))
    _expect(actual, expected, "upstream environment artifact closure")


def _sdk_artifact(environment: dict[str, JsonValue]) -> dict[str, JsonValue]:
    artifacts = environment.get("artifacts")
    if not isinstance(artifacts, list):
        raise UpstreamComparisonError("environment.artifacts must be an array.")
    matches = [
        item
        for item in artifacts
        if isinstance(item, dict) and item.get("distribution") == "openai-agents"
    ]
    if len(matches) != 1:
        raise UpstreamComparisonError("Environment must contain one openai-agents artifact.")
    return copy.deepcopy(matches[0])


def _validate_artifact(value: Any, *, label: str) -> dict[str, JsonValue]:
    artifact = _object(value, label)
    _exact_keys(artifact, _ARTIFACT_KEYS, label)
    distribution = artifact.get("distribution")
    version = artifact.get("version")
    filename = artifact.get("filename")
    if not isinstance(distribution, str) or not _DIST_NAME.fullmatch(distribution):
        raise UpstreamComparisonError(f"{label}.distribution is invalid.")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise UpstreamComparisonError(f"{label}.version is invalid.")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not _MEMBER_BASENAME.fullmatch(filename)
        or not filename.endswith(".whl")
    ):
        raise UpstreamComparisonError(f"{label}.filename must be a wheel basename.")
    size = artifact.get("size")
    digest = artifact.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise UpstreamComparisonError(f"{label}.size must be positive.")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise UpstreamComparisonError(f"{label}.sha256 is invalid.")
    return cast(dict[str, JsonValue], copy.deepcopy(artifact))


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
    if not isinstance(path, str) or Path(path).name != path or not _MEMBER_BASENAME.fullmatch(path):
        raise UpstreamComparisonError(f"{label}.path must be a basename.")
    if expected_path is not None and path != expected_path:
        raise UpstreamComparisonError(f"{label}.path must be {expected_path!r}.")
    size = member.get("size")
    digest = member.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise UpstreamComparisonError(f"{label}.size must be positive.")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise UpstreamComparisonError(f"{label}.sha256 is invalid.")
    if "certificate_id" in keys:
        certificate_id = member.get("certificate_id")
        if not isinstance(certificate_id, str) or not _PREFIXED_SHA256.fullmatch(certificate_id):
            raise UpstreamComparisonError(f"{label}.certificate_id is invalid.")
    return cast(dict[str, JsonValue], copy.deepcopy(member))


def _validate_optional_member(
    path: Path,
    member: Mapping[str, JsonValue],
    *,
    label: str,
) -> None:
    if not path.exists() and not path.is_symlink():
        return
    data = _required_member_bytes(path, label=label)
    _expect(len(data), member["size"], f"{label}.size")
    _expect(hashlib.sha256(data).hexdigest(), member["sha256"], f"{label}.sha256")


def _required_member_bytes(path: Path, *, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise UpstreamComparisonError(f"The {label} is missing or is a symbolic link.")
    try:
        return path.read_bytes()
    except OSError as error:
        raise UpstreamComparisonError(f"Cannot read {label}: {error}") from error


def _content_id(value: Any, field: str) -> str:
    if not isinstance(value, dict):
        raise UpstreamComparisonError("A content-addressed payload must be an object.")
    unsigned = copy.deepcopy(value)
    unsigned[field] = None
    try:
        return f"sha256:{sha256_hex(unsigned)}"
    except CanonicalizationError as error:
        raise UpstreamComparisonError(f"Payload is not canonical JSON: {error}") from error


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UpstreamComparisonError(f"{label} must be a JSON object.")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise UpstreamComparisonError(f"{label} fields differ; missing={missing}, extra={extra}.")


def _expect(actual: Any, expected: Any, label: str) -> None:
    if type(actual) is not type(expected) or actual != expected:
        raise UpstreamComparisonError(f"{label} does not match the comparison contract.")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UpstreamComparisonError(f"Duplicate JSON object key: {key!r}.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise UpstreamComparisonError(f"Non-finite JSON number is forbidden: {value}.")
