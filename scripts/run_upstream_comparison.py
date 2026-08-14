#!/usr/bin/env python3
from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import venv
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from agentrunproof._canonical import JsonValue, sha256_hex
from agentrunproof.certificate import write_certificate
from agentrunproof.current.comparison import (
    COMPARISON_SCHEMA_VERSION,
    RELEASE_TARGET_ID,
    RELEASE_WHEEL_SHA256,
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
    finalize_comparison_certificate,
    finalize_upstream_comparison,
    limitations,
    parse_worker_certificate_json,
    source_wheel_member_path,
    stable_worker_argv,
    write_upstream_comparison,
)
from agentrunproof.history.bundle import (
    ENVIRONMENT_SCHEMA_VERSION,
    file_member,
    finalize_environment,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD = ROOT / "build" / "upstream-comparison"
RELEASE_LOCK = ROOT / "requirements" / "history" / "openai-agents-0.20.0-linux-py312.txt"
UPSTREAM_LOCK = ROOT / "requirements" / "upstream" / ("openai-agents-0b93ce8-linux-py312.txt")
BUILD_LOCK = ROOT / "requirements" / "upstream" / "build-linux-py312.txt"
WORKER_TIMEOUT_SECONDS = 120
BUILD_TIMEOUT_SECONDS = 300
_WHEEL_METADATA = re.compile(r"^[^/]+\.dist-info/METADATA$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9.-]*$")

_SITE_CUSTOMIZE = """
import socket

def _deny_network(*_args, **_kwargs):
    raise RuntimeError("AgentRunProof comparison worker network access is disabled")

class _DeniedSocket(socket.socket):
    def connect(self, *_args, **_kwargs):
        return _deny_network()

    def connect_ex(self, *_args, **_kwargs):
        return _deny_network()

socket.socket = _DeniedSocket
socket.create_connection = _deny_network
socket.create_server = _deny_network
""".lstrip()

_ORIGIN_PROBE = r"""
import importlib.metadata
import json
from pathlib import Path
import agentrunproof
import agents

def distribution(name):
    item = importlib.metadata.distribution(name)
    direct = item.read_text("direct_url.json")
    return {
        "version": item.version,
        "direct_url": json.loads(direct) if direct is not None else None,
    }

print(json.dumps({
    "agentrunproof_module": str(Path(agentrunproof.__file__).resolve()),
    "agents_module": str(Path(agents.__file__).resolve()),
    "agentrunproof": distribution("agentrunproof"),
    "openai_agents": distribution("openai-agents"),
}, sort_keys=True))
""".strip()


@dataclass(frozen=True)
class PreparedEnvironment:
    python: Path
    pip_version: str
    sdk_direct_url: dict[str, JsonValue] | None


@dataclass(frozen=True)
class RunRequest:
    run_id: str
    target_id: str
    scenario_id: str
    certificate_path: str
    expected_exit: int
    expected_status: str


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.canonical:
        raise RuntimeError("Upstream comparison generation requires --canonical.")
    _validate_canonical_host()
    output = args.output_directory.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("Canonical comparison output must start as an empty directory.")
    output.mkdir(parents=True, exist_ok=True)

    source = _canonical_source(args.source_commit)
    source_checkouts = _source_checkouts(args)
    upstream_sources = {
        target_id: _upstream_source(checkout, target_id=target_id)
        for target_id, checkout in source_checkouts.items()
    }
    run_requests = _run_requests(args, source_target_ids=list(source_checkouts))

    with tempfile.TemporaryDirectory(prefix="agentrunproof-upstream-comparison-") as scratch_name:
        scratch = Path(scratch_name)
        harness_wheel = _build_harness_wheel(scratch / "harness-dist")
        build_python = _prepare_build_environment(scratch)
        source_wheels: dict[str, Path] = {}
        for target_id, upstream_checkout in source_checkouts.items():
            source_wheels[target_id] = _build_upstream_wheel(
                upstream_checkout,
                scratch=scratch / target_id,
                source_epoch=_git_at(
                    upstream_checkout,
                    "show",
                    "-s",
                    "--format=%ct",
                    "HEAD",
                ),
                build_python=build_python,
            )
        source_wheel_hashes = [_sha256_file(wheel) for wheel in source_wheels.values()]
        if RELEASE_WHEEL_SHA256 in source_wheel_hashes:
            raise RuntimeError("A source-built SDK wheel equals the released SDK wheel bytes.")
        if len(source_wheel_hashes) != len(set(source_wheel_hashes)):
            raise RuntimeError("Source targets unexpectedly built byte-identical SDK wheels.")

        release_wheelhouse = _download_closure(
            RELEASE_LOCK,
            scratch / "wheelhouse" / "released",
        )
        upstream_wheelhouse = _download_closure(
            UPSTREAM_LOCK,
            scratch / "wheelhouse" / "upstream",
        )
        release_artifacts = _wheelhouse_artifacts(release_wheelhouse)
        upstream_dependency_artifacts = _wheelhouse_artifacts(upstream_wheelhouse)
        source_artifacts = {
            target_id: _wheel_artifact(wheel) for target_id, wheel in source_wheels.items()
        }

        observations: list[dict[str, JsonValue]] = []
        pip_versions: dict[str, str] = {}
        normalized_source_direct_urls: dict[str, dict[str, JsonValue]] = {}
        for spec in run_requests:
            environment = _prepare_environment(
                spec.run_id,
                target_id=spec.target_id,
                harness_wheel=harness_wheel,
                source_wheel=source_wheels.get(spec.target_id),
                release_wheelhouse=release_wheelhouse,
                upstream_wheelhouse=upstream_wheelhouse,
                scratch=scratch,
            )
            previous_pip = pip_versions.setdefault(spec.target_id, environment.pip_version)
            if previous_pip != environment.pip_version:
                raise RuntimeError(
                    f"Target {spec.target_id} fresh venvs used different pip versions."
                )
            if spec.target_id != RELEASE_TARGET_ID:
                direct_url = environment.sdk_direct_url
                if direct_url is None:
                    raise RuntimeError("Source-built SDK direct_url was not observed.")
                previous_direct = normalized_source_direct_urls.setdefault(
                    spec.target_id,
                    direct_url,
                )
                if previous_direct != direct_url:
                    raise RuntimeError(
                        f"Target {spec.target_id} direct_url changed between fresh venvs."
                    )
            observations.append(
                _execute(
                    spec.run_id,
                    spec.scenario_id,
                    target_id=spec.target_id,
                    expected_exit=spec.expected_exit,
                    expected_status=spec.expected_status,
                    certificate_path=spec.certificate_path,
                    python=environment.python,
                    source_commit=_require_string(source["commit"]),
                    output=output,
                    scratch=scratch,
                )
            )

        all_target_ids = [RELEASE_TARGET_ID, *source_checkouts]
        if set(pip_versions) != set(all_target_ids):
            raise RuntimeError("Comparison runs did not prepare every target environment.")
        if set(normalized_source_direct_urls) != set(source_checkouts):
            raise RuntimeError("Comparison runs did not observe every source-built SDK direct_url.")
        release_environment = finalize_environment(
            {
                "schema_version": ENVIRONMENT_SCHEMA_VERSION,
                "sdk_version": "0.20.0",
                "python": platform.python_version(),
                "pip_version": pip_versions[RELEASE_TARGET_ID],
                "dependency_metadata_bypassed": False,
                "lock_sha256": _lock_digest(RELEASE_LOCK),
                "artifacts": release_artifacts,
            }
        )
        upstream_environments: dict[str, dict[str, JsonValue]] = {}
        for target_id in source_checkouts:
            artifacts = sorted(
                [*upstream_dependency_artifacts, source_artifacts[target_id]],
                key=lambda item: str(item["distribution"]),
            )
            upstream_environments[target_id] = finalize_environment(
                {
                    "schema_version": ENVIRONMENT_SCHEMA_VERSION,
                    "sdk_version": "0.20.0",
                    "python": platform.python_version(),
                    "pip_version": pip_versions[target_id],
                    "dependency_metadata_bypassed": False,
                    "lock_sha256": _lock_digest(UPSTREAM_LOCK),
                    "artifacts": artifacts,
                }
            )
            if upstream_environments[target_id]["lock_sha256"] != UPSTREAM_LOCK_SHA256:
                raise RuntimeError("The upstream dependency lock does not match the frozen digest.")

        _assert_source_stable(source)
        for target_id, checkout in source_checkouts.items():
            _assert_upstream_source_stable(
                checkout,
                upstream_sources[target_id],
                target_id=target_id,
            )

        published_harness_wheel = output / harness_wheel.name
        _atomic_bytes(published_harness_wheel, harness_wheel.read_bytes())
        published_source_wheels: dict[str, Path] = {}
        for target_id, wheel in source_wheels.items():
            commit = _require_string(upstream_sources[target_id]["commit"])
            published = output / source_wheel_member_path(commit)
            _atomic_bytes(published, wheel.read_bytes())
            published_source_wheels[target_id] = published

        release_wheel = _artifact_by_distribution(release_artifacts, "openai-agents")
        target_payloads: list[dict[str, JsonValue]] = [
            {
                "target_id": RELEASE_TARGET_ID,
                "kind": "pypi-wheel",
                "distribution": "openai-agents",
                "version": "0.20.0",
                "wheel": release_wheel,
                "environment": release_environment,
            }
        ]
        for target_id in source_checkouts:
            upstream_source = upstream_sources[target_id]
            source_wheel_payload = {
                **source_artifacts[target_id],
                "path": published_source_wheels[target_id].name,
                "direct_url": normalized_source_direct_urls[target_id],
            }
            target_payloads.append(
                {
                    "target_id": target_id,
                    "kind": "source-built-wheel",
                    "distribution": "openai-agents",
                    "version": "0.20.0",
                    "repository": upstream_source["repository"],
                    "commit": upstream_source["commit"],
                    "tree": upstream_source["tree"],
                    "parent": upstream_source["parent"],
                    "tracked_source_sha256": upstream_source["tracked_source_sha256"],
                    "pyproject_sha256": upstream_source["pyproject_sha256"],
                    "uv_lock_sha256": upstream_source["uv_lock_sha256"],
                    "source_wheel": source_wheel_payload,
                    "environment": upstream_environments[target_id],
                }
            )

        bundle = finalize_upstream_comparison(
            {
                "schema_version": COMPARISON_SCHEMA_VERSION,
                "source": source,
                "runtime": {
                    "python": platform.python_version(),
                    "implementation": platform.python_implementation(),
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "fresh_venvs": True,
                    "python_isolated": True,
                    "network_guard": "socket-deny-v1",
                    "worker_cwd": "empty-temporary-directory",
                    "environment_sanitized": True,
                    "worker_timeout_seconds": WORKER_TIMEOUT_SECONDS,
                },
                "harness": {"wheel": file_member(published_harness_wheel)},
                "targets": target_payloads,
                "runs": observations,
                "limitations": limitations(),
            },
            directory=output,
        )
        _assert_source_stable(source)
        for target_id, checkout in source_checkouts.items():
            _assert_upstream_source_stable(
                checkout,
                upstream_sources[target_id],
                target_id=target_id,
            )
        # The final bundle is the commit marker and is deliberately published last.
        write_upstream_comparison(output / "bundle.json", bundle)

    for observation in observations:
        print(
            f"{observation['observed_status']:4} {observation['run_id']} "
            f"exit={observation['worker_exit']}"
        )
    print(f"bundle: {output / 'bundle.json'}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the pinned released-versus-upstream AgentRunProof comparison."
    )
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--upstream-checkout", type=Path, required=True)
    parser.add_argument(
        "--additional-upstream-checkout",
        action="append",
        default=[],
        metavar="TARGET_ID=PATH",
        help="Add an official source checkout; may be repeated.",
    )
    parser.add_argument(
        "--additional-run",
        action="append",
        default=[],
        metavar="RUN_ID,TARGET_ID,SCENARIO_ID,STATUS,CERTIFICATE",
        help="Add a target observation after the audited #4413 baseline; may be repeated.",
    )
    parser.add_argument("--canonical", action="store_true")
    parser.add_argument("--source-commit", help="Expected clean AgentRunProof source commit.")
    return parser


def _source_checkouts(args: argparse.Namespace) -> dict[str, Path]:
    additional: dict[str, Path] = {}
    for raw in args.additional_upstream_checkout:
        if not isinstance(raw, str) or raw.count("=") != 1:
            raise RuntimeError("--additional-upstream-checkout must be TARGET_ID=PATH.")
        target_id, raw_path = raw.split("=", 1)
        if (
            not _IDENTIFIER.fullmatch(target_id)
            or target_id == RELEASE_TARGET_ID
            or target_id == UPSTREAM_TARGET_ID
            or not raw_path
        ):
            raise RuntimeError("An additional upstream target assignment is invalid.")
        if target_id in additional:
            raise RuntimeError(f"Duplicate additional upstream target: {target_id}.")
        additional[target_id] = Path(raw_path).resolve()
    result = {UPSTREAM_TARGET_ID: args.upstream_checkout.resolve()}
    result.update(dict(sorted(additional.items())))
    return result


def _run_requests(
    args: argparse.Namespace,
    *,
    source_target_ids: list[str],
) -> list[RunRequest]:
    requests = [
        RunRequest(
            spec.run_id,
            spec.target_id,
            spec.scenario_id,
            spec.certificate_path,
            spec.expected_exit,
            spec.expected_status,
        )
        for spec in RUN_SPECS
    ]
    target_order = {
        target_id: index for index, target_id in enumerate([RELEASE_TARGET_ID, *source_target_ids])
    }
    additions: list[RunRequest] = []
    for raw in args.additional_run:
        if not isinstance(raw, str):
            raise RuntimeError("--additional-run must be a comma-separated string.")
        fields = raw.split(",")
        if len(fields) != 5:
            raise RuntimeError(
                "--additional-run must contain RUN_ID,TARGET_ID,SCENARIO_ID,STATUS,CERTIFICATE."
            )
        run_id, target_id, scenario_id, expected_status, certificate_path = fields
        if not _IDENTIFIER.fullmatch(run_id) or not _IDENTIFIER.fullmatch(scenario_id):
            raise RuntimeError("An additional run or scenario ID is invalid.")
        if target_id not in source_target_ids:
            raise RuntimeError("An additional run must name a configured source target.")
        if expected_status not in {"PASS", "FAIL"}:
            raise RuntimeError("An additional run status must be PASS or FAIL.")
        if Path(certificate_path).name != certificate_path or not certificate_path.endswith(
            ".json"
        ):
            raise RuntimeError("An additional run certificate must be a JSON basename.")
        additions.append(
            RunRequest(
                run_id,
                target_id,
                scenario_id,
                certificate_path,
                0 if expected_status == "PASS" else 1,
                expected_status,
            )
        )
    additions.sort(key=lambda item: (target_order[item.target_id], item.run_id))
    requests.extend(additions)
    run_ids = [request.run_id for request in requests]
    certificate_paths = [request.certificate_path for request in requests]
    pairs = [(request.target_id, request.scenario_id) for request in requests]
    if len(run_ids) != len(set(run_ids)):
        raise RuntimeError("Every comparison run ID must be unique.")
    if len(certificate_paths) != len(set(certificate_paths)):
        raise RuntimeError("Every comparison certificate path must be unique.")
    if len(pairs) != len(set(pairs)):
        raise RuntimeError("Every comparison target/scenario pair must be unique.")
    referenced = {request.target_id for request in requests}
    if referenced != set(target_order):
        raise RuntimeError("Every configured comparison target must have at least one run.")
    return requests


def _build_harness_wheel(distribution_dir: Path) -> Path:
    distribution_dir.mkdir(parents=True, exist_ok=False)
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(distribution_dir),
        ],
        cwd=ROOT,
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    wheels = sorted(distribution_dir.glob("agentrunproof-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one AgentRunProof wheel, found {len(wheels)}.")
    return wheels[0]


def _build_upstream_wheel(
    checkout: Path,
    *,
    scratch: Path,
    source_epoch: str,
    build_python: Path,
) -> Path:
    scratch.mkdir(parents=True, exist_ok=False)
    archive = scratch / "upstream-source.tar"
    build_source = scratch / "upstream-build-source"
    build_source.mkdir()
    _run(
        [
            "git",
            "archive",
            "--format=tar",
            "--output",
            str(archive),
            "HEAD",
        ],
        cwd=checkout,
        timeout=60,
    )
    with tarfile.open(archive, "r:") as source_archive:
        source_archive.extractall(build_source, filter="data")
    distribution_dir = scratch / "upstream-dist"
    distribution_dir.mkdir()
    build_environment = dict(os.environ)
    build_environment["SOURCE_DATE_EPOCH"] = source_epoch
    _run(
        [
            str(build_python),
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(distribution_dir),
        ],
        cwd=build_source,
        timeout=BUILD_TIMEOUT_SECONDS,
        env=build_environment,
    )
    wheels = sorted(distribution_dir.glob("openai_agents-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one source-built openai-agents wheel, found {len(wheels)}.")
    artifact = _wheel_artifact(wheels[0])
    if artifact["distribution"] != "openai-agents" or artifact["version"] != "0.20.0":
        raise RuntimeError("Source checkout did not build the expected openai-agents 0.20.0 wheel.")
    return wheels[0]


def _prepare_build_environment(scratch: Path) -> Path:
    wheelhouse = _download_closure(BUILD_LOCK, scratch / "wheelhouse" / "build")
    environment = scratch / "build-venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _install_lock(python, BUILD_LOCK, wheelhouse)
    versions = _capture(
        [
            str(python),
            "-I",
            "-c",
            ("import importlib.metadata as m;print(m.version('build'), m.version('hatchling'))"),
        ],
        cwd=ROOT,
    )
    if versions != "1.5.0 1.26.3":
        raise RuntimeError("Canonical upstream build tools do not match the pinned versions.")
    return python


def _download_closure(lock: Path, wheelhouse: Path) -> Path:
    wheelhouse.mkdir(parents=True, exist_ok=False)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--require-hashes",
            "--only-binary=:all:",
            "--dest",
            str(wheelhouse),
            "--requirement",
            str(lock),
        ],
        cwd=ROOT,
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    if not any(wheelhouse.glob("*.whl")):
        raise RuntimeError(f"Dependency lock produced no wheels: {lock}.")
    return wheelhouse


def _prepare_environment(
    run_id: str,
    *,
    target_id: str,
    harness_wheel: Path,
    source_wheel: Path | None,
    release_wheelhouse: Path,
    upstream_wheelhouse: Path,
    scratch: Path,
) -> PreparedEnvironment:
    environment = scratch / "venvs" / run_id
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if target_id == RELEASE_TARGET_ID:
        if source_wheel is not None:
            raise RuntimeError("Released target cannot be paired with a source wheel.")
        _install_lock(python, RELEASE_LOCK, release_wheelhouse)
    else:
        if source_wheel is None:
            raise RuntimeError(f"Source target {target_id} has no built wheel.")
        _install_lock(python, UPSTREAM_LOCK, upstream_wheelhouse)
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-index",
                "--no-deps",
                str(source_wheel),
            ],
            cwd=ROOT,
            timeout=BUILD_TIMEOUT_SECONDS,
        )
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--no-deps",
            str(harness_wheel),
        ],
        cwd=ROOT,
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    _run(
        [str(python), "-m", "pip", "check"],
        cwd=ROOT,
        timeout=60,
    )
    probe = _origin_probe(python, environment=environment)
    _validate_harness_origin(probe, environment=environment, wheel=harness_wheel)
    sdk_direct_url = _validate_sdk_origin(
        probe,
        environment=environment,
        target_id=target_id,
        source_wheel=source_wheel,
    )
    _install_socket_guard(python)
    _assert_socket_guard(python)
    return PreparedEnvironment(
        python=python,
        pip_version=_pip_version(python),
        sdk_direct_url=sdk_direct_url,
    )


def _install_lock(python: Path, lock: Path, wheelhouse: Path) -> None:
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--require-hashes",
            "--requirement",
            str(lock),
        ],
        cwd=ROOT,
        timeout=BUILD_TIMEOUT_SECONDS,
    )


def _execute(
    run_id: str,
    scenario_id: str,
    *,
    target_id: str,
    expected_exit: int,
    expected_status: str,
    certificate_path: str,
    python: Path,
    source_commit: str,
    output: Path,
    scratch: Path,
) -> dict[str, JsonValue]:
    worker_cwd = Path(tempfile.mkdtemp(prefix=f"worker-{run_id}-", dir=scratch))
    stable_argv = stable_worker_argv(scenario_id)
    completed = subprocess.run(
        [str(python), *stable_argv[1:]],
        cwd=worker_cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
        timeout=WORKER_TIMEOUT_SECONDS,
    )
    if completed.stderr:
        raise RuntimeError(f"Comparison worker {run_id} wrote stderr: {completed.stderr}")
    if completed.returncode != expected_exit:
        raise RuntimeError(
            f"Comparison worker {run_id} exited {completed.returncode}, expected {expected_exit}."
        )
    certificate = parse_worker_certificate_json(completed.stdout)
    if certificate.get("overall_status") != expected_status:
        raise RuntimeError(
            f"Comparison worker {run_id} observed {certificate.get('overall_status')}, "
            f"expected {expected_status}."
        )
    certificate = finalize_comparison_certificate(certificate, source_commit=source_commit)
    path = output / certificate_path
    write_certificate(path, certificate)
    member = file_member(path)
    member["certificate_id"] = certificate["certificate_id"]
    return {
        "run_id": run_id,
        "target_id": target_id,
        "scenario_id": scenario_id,
        "argv": cast(JsonValue, stable_argv),
        "expected_exit": expected_exit,
        "worker_exit": completed.returncode,
        "expected_status": expected_status,
        "observed_status": certificate["overall_status"],
        "certificate": member,
    }


def _origin_probe(python: Path, *, environment: Path) -> dict[str, Any]:
    output = _capture([str(python), "-I", "-c", _ORIGIN_PROBE], cwd=ROOT)
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise RuntimeError("Installed wheel origin probe did not return JSON.") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Installed wheel origin probe did not return an object.")
    for key in ("agentrunproof_module", "agents_module"):
        path = Path(_require_string(payload.get(key))).resolve()
        try:
            path.relative_to(environment.resolve())
        except ValueError as error:
            raise RuntimeError(f"{key} was not imported from the fresh environment.") from error
        if "site-packages" not in path.parts:
            raise RuntimeError(f"{key} was not imported from site-packages.")
    return payload


def _validate_harness_origin(probe: dict[str, Any], *, environment: Path, wheel: Path) -> None:
    del environment
    harness = _require_object(probe.get("agentrunproof"), "agentrunproof origin")
    direct_url = _require_object(harness.get("direct_url"), "agentrunproof direct_url")
    normalized = _normalize_direct_url(direct_url, expected_wheel=wheel)
    if normalized["archive_sha256"] != _sha256_file(wheel):
        raise RuntimeError("Installed AgentRunProof does not match the built harness wheel.")


def _validate_sdk_origin(
    probe: dict[str, Any],
    *,
    environment: Path,
    target_id: str,
    source_wheel: Path | None,
) -> dict[str, JsonValue] | None:
    del environment
    sdk = _require_object(probe.get("openai_agents"), "openai-agents origin")
    if sdk.get("version") != "0.20.0":
        raise RuntimeError("Installed openai-agents does not report version 0.20.0.")
    direct_url = sdk.get("direct_url")
    if target_id == RELEASE_TARGET_ID:
        if direct_url is not None:
            raise RuntimeError(
                "Released SDK target must be installed from its locked PyPI closure."
            )
        return None
    if source_wheel is None:
        raise RuntimeError(f"Source target {target_id} has no wheel for origin validation.")
    direct = _require_object(direct_url, "source-built openai-agents direct_url")
    return _normalize_direct_url(direct, expected_wheel=source_wheel)


def _normalize_direct_url(
    value: dict[str, Any],
    *,
    expected_wheel: Path,
) -> dict[str, JsonValue]:
    url = value.get("url")
    archive_info = _require_object(value.get("archive_info"), "direct_url.archive_info")
    hashes = _require_object(archive_info.get("hashes"), "direct_url.archive_info.hashes")
    digest = hashes.get("sha256")
    if not isinstance(url, str) or not isinstance(digest, str):
        raise RuntimeError("Installed wheel direct_url lacks URL or SHA-256.")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "file":
        raise RuntimeError("Installed source wheel direct_url must use the file scheme.")
    installed_path = Path(urllib.parse.unquote(parsed.path)).resolve()
    if installed_path != expected_wheel.resolve():
        raise RuntimeError("Installed direct_url does not point to the expected wheel.")
    expected_digest = _sha256_file(expected_wheel)
    if digest != expected_digest:
        raise RuntimeError("Installed direct_url hash does not match the expected wheel.")
    return {
        "url_scheme": "file",
        "url_basename": expected_wheel.name,
        "archive_sha256": digest,
    }


def _install_socket_guard(python: Path) -> None:
    purelib = Path(
        _capture(
            [
                str(python),
                "-I",
                "-c",
                "import sysconfig;print(sysconfig.get_path('purelib'))",
            ],
            cwd=ROOT,
        )
    )
    sitecustomize = purelib / "sitecustomize.py"
    if sitecustomize.exists() or sitecustomize.is_symlink():
        raise RuntimeError("Fresh environment unexpectedly contains sitecustomize.py.")
    sitecustomize.write_text(_SITE_CUSTOMIZE, encoding="utf-8")


def _assert_socket_guard(python: Path) -> None:
    output = _capture(
        [
            str(python),
            "-I",
            "-c",
            (
                "import socket;"
                "\ntry: socket.create_connection(('127.0.0.1', 9))"
                "\nexcept RuntimeError: print('DENIED')"
                "\nelse: raise SystemExit('socket guard inactive')"
            ),
        ],
        cwd=ROOT,
    )
    if output != "DENIED":
        raise RuntimeError("The comparison worker socket guard is inactive.")


def _canonical_source(expected_commit: str | None) -> dict[str, JsonValue]:
    commit = _git_at(ROOT, "rev-parse", "HEAD")
    tree = _git_at(ROOT, "rev-parse", "HEAD^{tree}")
    status = _git_at(ROOT, "status", "--porcelain=v1", "--untracked-files=all")
    index = _git_at(ROOT, "ls-files", "-v")
    if expected_commit is not None and commit != expected_commit:
        raise RuntimeError(f"HEAD {commit} does not match --source-commit {expected_commit}.")
    hidden_flags = [
        line for line in index.splitlines() if line and (line[0].islower() or line[0] == "S")
    ]
    if status or hidden_flags:
        raise RuntimeError("Canonical comparison requires a clean harness worktree and index.")
    return {
        "commit": commit,
        "tree": tree,
        "clean": True,
        "index_flags_clean": True,
    }


def _assert_source_stable(source: dict[str, JsonValue]) -> None:
    commit = _require_string(source.get("commit"))
    if _canonical_source(commit) != source:
        raise RuntimeError("Harness source provenance changed during comparison generation.")


def _upstream_source(checkout: Path, *, target_id: str) -> dict[str, JsonValue]:
    commit = _git_at(checkout, "rev-parse", "HEAD")
    tree = _git_at(checkout, "rev-parse", "HEAD^{tree}")
    parent_line = _git_at(checkout, "rev-list", "--parents", "-n", "1", "HEAD").split()
    status = _git_at(checkout, "status", "--porcelain=v1", "--untracked-files=all")
    index = _git_at(checkout, "ls-files", "-v")
    remote = _normalize_repository(_git_at(checkout, "remote", "get-url", "origin"))
    hidden_flags = [
        line for line in index.splitlines() if line and (line[0].islower() or line[0] == "S")
    ]
    if status or hidden_flags:
        raise RuntimeError("Canonical comparison requires a clean upstream checkout and index.")
    expected_target_id = (
        UPSTREAM_TARGET_ID if commit == UPSTREAM_COMMIT else f"openai-agents-python-{commit[:8]}"
    )
    if target_id != expected_target_id:
        raise RuntimeError("Upstream target ID does not identify its checkout commit.")
    if len(parent_line) != 2 or parent_line[0] != commit:
        raise RuntimeError("An upstream comparison target must have exactly one parent.")
    parent = parent_line[1]
    if remote != UPSTREAM_REPOSITORY:
        raise RuntimeError("Upstream checkout origin does not match the official repository.")
    pyproject_sha256 = _sha256_file(checkout / "pyproject.toml")
    uv_lock_sha256 = _sha256_file(checkout / "uv.lock")
    if pyproject_sha256 != UPSTREAM_PYPROJECT_SHA256 or uv_lock_sha256 != UPSTREAM_UV_LOCK_SHA256:
        raise RuntimeError(
            "An upstream source target requires the audited pyproject.toml and uv.lock."
        )
    tracked = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
        cwd=checkout,
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    tracked_source_sha256 = hashlib.sha256(tracked).hexdigest()
    if target_id == UPSTREAM_TARGET_ID:
        expected_baseline = {
            "commit": UPSTREAM_COMMIT,
            "tree": UPSTREAM_TREE,
            "parent": UPSTREAM_PARENT,
            "tracked_source_sha256": UPSTREAM_TRACKED_SOURCE_SHA256,
            "pyproject_sha256": UPSTREAM_PYPROJECT_SHA256,
            "uv_lock_sha256": UPSTREAM_UV_LOCK_SHA256,
        }
        actual_baseline = {
            "commit": commit,
            "tree": tree,
            "parent": parent,
            "tracked_source_sha256": tracked_source_sha256,
            "pyproject_sha256": pyproject_sha256,
            "uv_lock_sha256": uv_lock_sha256,
        }
        if actual_baseline != expected_baseline:
            raise RuntimeError("The audited #4413 source target does not match its pinned hashes.")
    return {
        "repository": UPSTREAM_REPOSITORY,
        "commit": commit,
        "tree": tree,
        "parent": parent,
        "tracked_source_sha256": tracked_source_sha256,
        "pyproject_sha256": pyproject_sha256,
        "uv_lock_sha256": uv_lock_sha256,
        "clean": True,
        "index_flags_clean": True,
    }


def _assert_upstream_source_stable(
    checkout: Path,
    expected: dict[str, JsonValue],
    *,
    target_id: str,
) -> None:
    if _upstream_source(checkout, target_id=target_id) != expected:
        raise RuntimeError("Upstream source provenance changed during comparison generation.")


def _validate_canonical_host() -> None:
    if (
        platform.python_implementation() != "CPython"
        or platform.python_version() != "3.12.13"
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise RuntimeError("Canonical comparison requires Linux x86_64 CPython 3.12.13.")


def _normalize_repository(value: str) -> str:
    normalized = value.removesuffix(".git").rstrip("/")
    if normalized.startswith("git@github.com:"):
        normalized = "https://github.com/" + normalized.removeprefix("git@github.com:")
    return normalized


def _wheelhouse_artifacts(wheelhouse: Path) -> list[dict[str, JsonValue]]:
    artifacts = sorted(
        (_wheel_artifact(path) for path in wheelhouse.glob("*.whl")),
        key=lambda item: str(item["distribution"]),
    )
    distributions = [item["distribution"] for item in artifacts]
    if len(distributions) != len(set(distributions)):
        raise RuntimeError("A locked wheel closure contains duplicate distributions.")
    return artifacts


def _wheel_artifact(path: Path) -> dict[str, JsonValue]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Wheel artifact is missing or a symlink: {path}.")
    with zipfile.ZipFile(path) as archive:
        metadata_names = [name for name in archive.namelist() if _WHEEL_METADATA.fullmatch(name)]
        if len(metadata_names) != 1:
            raise RuntimeError(f"Wheel does not contain one METADATA file: {path.name}.")
        message = email.message_from_bytes(archive.read(metadata_names[0]))
    raw_name = message.get("Name")
    version = message.get("Version")
    if not raw_name or not version:
        raise RuntimeError(f"Wheel metadata lacks Name or Version: {path.name}.")
    return {
        "distribution": re.sub(r"[-_.]+", "-", raw_name).lower(),
        "version": version,
        "filename": path.name,
        "size": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _artifact_by_distribution(
    artifacts: list[dict[str, JsonValue]], distribution: str
) -> dict[str, JsonValue]:
    matches = [item for item in artifacts if item.get("distribution") == distribution]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {distribution} artifact in the closure.")
    return matches[0]


def _lock_digest(path: Path) -> str:
    seen: set[Path] = set()
    records: list[dict[str, str]] = []

    def visit(candidate: Path) -> None:
        candidate = candidate.resolve()
        if candidate in seen:
            return
        seen.add(candidate)
        text = candidate.read_text(encoding="utf-8")
        records.append(
            {
                "path": str(candidate.relative_to(ROOT)),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("-r ") or stripped.startswith("--requirement "):
                visit(candidate.parent / stripped.split(maxsplit=1)[1])

    visit(path)
    return sha256_hex(sorted(records, key=lambda item: item["path"]))


def _pip_version(python: Path) -> str:
    output = _capture([str(python), "-m", "pip", "--version"], cwd=ROOT)
    fields = output.split()
    if len(fields) < 2 or fields[0] != "pip":
        raise RuntimeError(f"Cannot parse pip version: {output!r}.")
    return fields[1]


def _capture(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError(f"Command did not produce clean output: {command!r}.")
    return completed.stdout.strip()


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False, timeout=timeout, env=env)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit {completed.returncode}: {command!r}")


def _git_at(directory: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=directory,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError(f"Git command failed in {directory}: {arguments!r}.")
    return completed.stdout.strip()


def _atomic_bytes(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_string(value: Any) -> str:
    if not isinstance(value, str):
        raise RuntimeError("Expected a string provenance value.")
    return value


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object.")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
