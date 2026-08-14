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
from .evidence import HistoryEvidenceError, parse_history_matrix_json

BUNDLE_SCHEMA_VERSION: Final = "agentrunproof.history-bundle/v1"
ENVIRONMENT_SCHEMA_VERSION: Final = "agentrunproof.history-environment/v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_PYTHON = re.compile(r"^3\.12\.[0-9]+$")
_DIST_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.!+~-]*$")
_WHEEL = re.compile(r"^agentrunproof-[A-Za-z0-9][A-Za-z0-9_.!+]*-py3-none-any\.whl$")
_SDK_VERSIONS = ("0.19.2", "0.19.3", "0.19.4", "0.20.0")
_LIMITATIONS = [
    "This bundle is content-addressed and Git-anchored, not cryptographically signed.",
    "The socket-deny guard covers Python worker execution, not artifact acquisition.",
    "The AgentRunProof wheel is hash-bound and published with CI/release artifacts, not committed beside this marker.",
]

_BUNDLE_KEYS = {
    "schema_version",
    "bundle_id",
    "source",
    "runtime",
    "wheel",
    "environments",
    "matrix",
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
_MATRIX_MEMBER_KEYS = {*_MEMBER_KEYS, "matrix_id"}
_ENVIRONMENT_KEYS = {
    "schema_version",
    "environment_id",
    "sdk_version",
    "python",
    "pip_version",
    "dependency_metadata_bypassed",
    "lock_sha256",
    "artifacts",
}
_ARTIFACT_KEYS = {"distribution", "version", "filename", "size", "sha256"}


class HistoryBundleError(ValueError):
    """Raised when a historical evidence bundle is incomplete or inconsistent."""


def finalize_environment(value: Any) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise HistoryBundleError("A history environment must be a JSON object.")
    candidate = copy.deepcopy(value)
    if "environment_id" not in candidate:
        candidate["environment_id"] = None
    elif candidate["environment_id"] is not None:
        raise HistoryBundleError("An environment being finalized must have a null ID.")
    candidate["environment_id"] = _content_id(candidate, "environment_id")
    return _validate_environment(candidate)


def validate_environment(value: Any) -> dict[str, JsonValue]:
    """Validate one already-addressed historical environment closure."""

    return _validate_environment(value)


def finalize_history_bundle(value: Any, *, directory: Path) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise HistoryBundleError("The history bundle must be a JSON object.")
    candidate = copy.deepcopy(value)
    if "bundle_id" not in candidate:
        candidate["bundle_id"] = None
    elif candidate["bundle_id"] is not None:
        raise HistoryBundleError("A bundle being finalized must have a null bundle_id.")
    environments = candidate.get("environments")
    if not isinstance(environments, list):
        raise HistoryBundleError("environments must be an array.")
    candidate["environments"] = [finalize_environment(item) for item in environments]
    candidate["bundle_id"] = _content_id(candidate, "bundle_id")
    return validate_history_bundle(candidate, directory=directory)


def validate_history_bundle(value: Any, *, directory: Path) -> dict[str, JsonValue]:
    bundle = _object(value, "history bundle")
    _exact_keys(bundle, _BUNDLE_KEYS, "history bundle")
    _expect(bundle.get("schema_version"), BUNDLE_SCHEMA_VERSION, "schema_version")
    bundle_id = bundle.get("bundle_id")
    if not isinstance(bundle_id, str) or not _PREFIXED_SHA256.fullmatch(bundle_id):
        raise HistoryBundleError("bundle_id must be a lowercase SHA-256 identifier.")
    if bundle_id != _content_id(bundle, "bundle_id"):
        raise HistoryBundleError("bundle_id does not match the canonical bundle payload.")
    _validate_source(bundle.get("source"))
    runtime_python = _validate_runtime(bundle.get("runtime"))
    wheel = _validate_file_member(bundle.get("wheel"), label="wheel", expected_path=None)
    wheel_name = cast(str, wheel["path"])
    if not _WHEEL.fullmatch(wheel_name):
        raise HistoryBundleError("wheel.path must be the canonical AgentRunProof wheel name.")
    wheel_path = directory / wheel_name
    if wheel_path.exists() or wheel_path.is_symlink():
        if wheel_path.is_symlink() or not wheel_path.is_file():
            raise HistoryBundleError("The optional wheel member must be a regular file.")
        wheel_bytes = wheel_path.read_bytes()
        _expect(len(wheel_bytes), wheel["size"], "wheel.size")
        _expect(hashlib.sha256(wheel_bytes).hexdigest(), wheel["sha256"], "wheel.sha256")

    environments = bundle.get("environments")
    if not isinstance(environments, list) or len(environments) != len(_SDK_VERSIONS):
        raise HistoryBundleError("environments must contain exactly four SDK closures.")
    validated_environments = [_validate_environment(item) for item in environments]
    actual_versions = [cast(str, item["sdk_version"]) for item in validated_environments]
    if actual_versions != list(_SDK_VERSIONS):
        raise HistoryBundleError("environments must be sorted by the pinned SDK versions.")
    if any(item["python"] != runtime_python for item in validated_environments):
        raise HistoryBundleError("Every environment must use the bundle runtime Python.")

    matrix_member = _validate_file_member(
        bundle.get("matrix"),
        label="matrix",
        expected_path="matrix.json",
        keys=_MATRIX_MEMBER_KEYS,
    )
    matrix_path = directory / "matrix.json"
    if matrix_path.is_symlink() or not matrix_path.is_file():
        raise HistoryBundleError("matrix.json is missing or is a symbolic link.")
    matrix_bytes = matrix_path.read_bytes()
    _expect(len(matrix_bytes), matrix_member["size"], "matrix.size")
    _expect(hashlib.sha256(matrix_bytes).hexdigest(), matrix_member["sha256"], "matrix.sha256")
    try:
        matrix = parse_history_matrix_json(matrix_bytes.decode("utf-8"))
    except (UnicodeError, HistoryEvidenceError) as error:
        raise HistoryBundleError(f"matrix.json is invalid: {error}") from error
    _expect(matrix.get("matrix_id"), matrix_member.get("matrix_id"), "matrix.matrix_id")
    matrix_wheel = _object(matrix.get("wheel"), "matrix.wheel")
    _expect(matrix_wheel.get("name"), wheel_name, "matrix.wheel.name")
    _expect(matrix_wheel.get("sha256"), wheel.get("sha256"), "matrix.wheel.sha256")
    worker_versions = {
        cast(str, _object(run, "matrix run").get("sdk_version"))
        for run in cast(list[Any], matrix["runs"])
    }
    if worker_versions != set(_SDK_VERSIONS):
        raise HistoryBundleError("Matrix SDK versions do not match the environment closures.")
    _expect(bundle.get("limitations"), _LIMITATIONS, "limitations")
    return cast(dict[str, JsonValue], copy.deepcopy(bundle))


def load_history_bundle(path: Path) -> dict[str, JsonValue]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except HistoryBundleError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise HistoryBundleError(f"Cannot read history bundle: {error}") from error
    return validate_history_bundle(payload, directory=path.parent)


def history_bundle_json(value: Any, *, directory: Path) -> str:
    bundle = validate_history_bundle(value, directory=directory)
    return json.dumps(bundle, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_history_bundle(path: Path, value: Any) -> None:
    rendered = history_bundle_json(value, directory=path.parent)
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


def file_member(path: Path) -> dict[str, JsonValue]:
    if path.is_symlink() or not path.is_file():
        raise HistoryBundleError(f"Evidence member is missing or a symlink: {path}.")
    data = path.read_bytes()
    return {
        "path": path.name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def limitations() -> list[str]:
    return list(_LIMITATIONS)


def _validate_source(value: Any) -> None:
    source = _object(value, "source")
    _exact_keys(source, _SOURCE_KEYS, "source")
    for key in ("commit", "tree"):
        item = source.get(key)
        if not isinstance(item, str) or not _GIT_OBJECT.fullmatch(item):
            raise HistoryBundleError(f"source.{key} must be a 40-character Git object ID.")
    if source.get("clean") is not True or source.get("index_flags_clean") is not True:
        raise HistoryBundleError("Canonical history evidence requires a clean source and index.")


def _validate_runtime(value: Any) -> str:
    runtime = _object(value, "runtime")
    _exact_keys(runtime, _RUNTIME_KEYS, "runtime")
    python = runtime.get("python")
    if not isinstance(python, str) or not _PYTHON.fullmatch(python):
        raise HistoryBundleError("runtime.python must be a CPython 3.12 release.")
    expected = {
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
    return python


def _validate_environment(value: Any) -> dict[str, JsonValue]:
    environment = _object(value, "environment")
    _exact_keys(environment, _ENVIRONMENT_KEYS, "environment")
    _expect(environment.get("schema_version"), ENVIRONMENT_SCHEMA_VERSION, "environment schema")
    identifier = environment.get("environment_id")
    if not isinstance(identifier, str) or not _PREFIXED_SHA256.fullmatch(identifier):
        raise HistoryBundleError("environment_id must be a lowercase SHA-256 identifier.")
    if identifier != _content_id(environment, "environment_id"):
        raise HistoryBundleError("environment_id does not match its canonical payload.")
    sdk_version = environment.get("sdk_version")
    if sdk_version not in _SDK_VERSIONS:
        raise HistoryBundleError("environment.sdk_version is not pinned by bundle v1.")
    python = environment.get("python")
    if not isinstance(python, str) or not _PYTHON.fullmatch(python):
        raise HistoryBundleError("environment.python must be a CPython 3.12 release.")
    pip_version = environment.get("pip_version")
    if not isinstance(pip_version, str) or not _VERSION.fullmatch(pip_version):
        raise HistoryBundleError("environment.pip_version must be a release version.")
    expected_bypass = sdk_version != "0.20.0"
    _expect(
        environment.get("dependency_metadata_bypassed"),
        expected_bypass,
        "environment.dependency_metadata_bypassed",
    )
    lock_digest = environment.get("lock_sha256")
    if not isinstance(lock_digest, str) or not _SHA256.fullmatch(lock_digest):
        raise HistoryBundleError("environment.lock_sha256 must be a lowercase SHA-256 digest.")
    artifacts = environment.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise HistoryBundleError("environment.artifacts must be a non-empty array.")
    distributions: list[str] = []
    sdk_artifact_count = 0
    for artifact_value in artifacts:
        artifact = _object(artifact_value, "environment artifact")
        _exact_keys(artifact, _ARTIFACT_KEYS, "environment artifact")
        distribution = artifact.get("distribution")
        version = artifact.get("version")
        filename = artifact.get("filename")
        if not isinstance(distribution, str) or not _DIST_NAME.fullmatch(distribution):
            raise HistoryBundleError("Invalid environment artifact distribution.")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise HistoryBundleError("Invalid environment artifact version.")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".whl")
        ):
            raise HistoryBundleError("Invalid environment artifact filename.")
        size = artifact.get("size")
        digest = artifact.get("sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise HistoryBundleError("Environment artifact size must be positive.")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise HistoryBundleError("Invalid environment artifact SHA-256 digest.")
        distributions.append(distribution)
        if distribution == "openai-agents" and version == sdk_version:
            sdk_artifact_count += 1
    if distributions != sorted(distributions) or len(distributions) != len(set(distributions)):
        raise HistoryBundleError("Environment artifacts must be unique and distribution-sorted.")
    if sdk_artifact_count != 1:
        raise HistoryBundleError("Environment closure must contain its exact openai-agents wheel.")
    return cast(dict[str, JsonValue], copy.deepcopy(environment))


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
        raise HistoryBundleError(f"{label}.path must be a basename.")
    if expected_path is not None and path != expected_path:
        raise HistoryBundleError(f"{label}.path must be {expected_path!r}.")
    size = member.get("size")
    digest = member.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise HistoryBundleError(f"{label}.size must be positive.")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise HistoryBundleError(f"{label}.sha256 must be a lowercase digest.")
    if "matrix_id" in keys:
        matrix_id = member.get("matrix_id")
        if not isinstance(matrix_id, str) or not _PREFIXED_SHA256.fullmatch(matrix_id):
            raise HistoryBundleError("matrix.matrix_id must be a lowercase identifier.")
    return cast(dict[str, JsonValue], copy.deepcopy(member))


def _content_id(value: Any, field: str) -> str:
    if not isinstance(value, dict):
        raise HistoryBundleError("A content-addressed payload must be an object.")
    unsigned = copy.deepcopy(value)
    unsigned[field] = None
    try:
        return f"sha256:{sha256_hex(unsigned)}"
    except CanonicalizationError as error:
        raise HistoryBundleError(f"Bundle payload is not canonical JSON: {error}") from error


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HistoryBundleError(f"{label} must be a JSON object.")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise HistoryBundleError(f"{label} fields differ; missing={missing}, extra={extra}.")


def _expect(actual: Any, expected: Any, label: str) -> None:
    if type(actual) is not type(expected) or actual != expected:
        raise HistoryBundleError(f"{label} does not match the bundle contract.")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HistoryBundleError(f"Duplicate JSON object key: {key!r}.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise HistoryBundleError(f"Non-finite JSON number is forbidden: {value}.")
