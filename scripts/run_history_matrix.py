#!/usr/bin/env python3
from __future__ import annotations

import argparse
import email
import hashlib
import os
import platform
import re
import subprocess
import sys
import tempfile
import venv
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentrunproof._canonical import JsonValue, sha256_hex
from agentrunproof.history.bundle import (
    BUNDLE_SCHEMA_VERSION,
    ENVIRONMENT_SCHEMA_VERSION,
    file_member,
    finalize_history_bundle,
    limitations,
    write_history_bundle,
)
from agentrunproof.history.evidence import (
    finalize_history_matrix,
    history_matrix_json,
    parse_history_worker_json,
    validate_history_matrix,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD = ROOT / "build" / "history"
LOCKS = ROOT / "requirements" / "history"
WORKER_TIMEOUT_SECONDS = 120
_WHEEL_METADATA = re.compile(r"^[^/]+\.dist-info/METADATA$")

_NETWORK_GUARDED_WORKER = """
import socket
import sys

def _deny_network(*_args, **_kwargs):
    raise RuntimeError("AgentRunProof history worker network access is disabled")

class _DeniedSocket(socket.socket):
    def connect(self, *_args, **_kwargs):
        return _deny_network()

    def connect_ex(self, *_args, **_kwargs):
        return _deny_network()

socket.socket = _DeniedSocket
socket.create_connection = _deny_network
socket.create_server = _deny_network

from agentrunproof.history.worker import main
raise SystemExit(main([sys.argv[1]]))
""".strip()


@dataclass(frozen=True)
class MatrixRun:
    case_id: str
    sdk_version: str
    expected: str


@dataclass(frozen=True)
class PreparedEnvironment:
    python: Path
    manifest: dict[str, JsonValue] | None


RUNS = (
    MatrixRun("session-limit-orphan-output", "0.19.4", "FAIL"),
    MatrixRun("session-limit-orphan-output", "0.20.0", "PASS"),
    MatrixRun("runstate-context-approval", "0.19.4", "FAIL"),
    MatrixRun("runstate-context-approval", "0.20.0", "PASS"),
    MatrixRun("resumed-guardrail-atomicity", "0.19.2", "FAIL"),
    MatrixRun("resumed-guardrail-atomicity", "0.19.3", "PASS"),
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.output_directory.resolve()
    if args.canonical:
        _validate_canonical_host()
        for name in ("matrix.json", "bundle.json"):
            if (output / name).exists():
                raise RuntimeError(
                    f"Canonical output must start empty; choose a new directory ({name} exists)."
                )
        source = _canonical_source(args.source_commit)
    else:
        source = None
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="agentrunproof-history-") as scratch_name:
        scratch = Path(scratch_name)
        wheel = _build_wheel(scratch / "dist")
        prepared = {
            version: _prepare_environment(
                version,
                wheel=wheel,
                scratch=scratch,
                canonical=args.canonical,
            )
            for version in sorted({run.sdk_version for run in RUNS})
        }
        observations = [
            _execute(run, prepared[run.sdk_version].python, scratch=scratch) for run in RUNS
        ]
        payload = finalize_history_matrix(
            {
                "schema_version": "agentrunproof.history-matrix/v1",
                "wheel": {"name": wheel.name, "sha256": _sha256_file(wheel)},
                "runs": observations,
                "expectations_matched": True,
            }
        )
        validate_history_matrix(payload)
        if source is not None:
            _assert_source_stable(source)

        published_wheel = wheel
        if source is not None:
            published_wheel = output / wheel.name
            _atomic_bytes(published_wheel, wheel.read_bytes())
        matrix_path = output / "matrix.json"
        _atomic_text(matrix_path, history_matrix_json(payload))

        if source is not None:
            environments = [
                prepared[version].manifest for version in ("0.19.2", "0.19.3", "0.19.4", "0.20.0")
            ]
            if any(item is None for item in environments):
                raise RuntimeError("Canonical environments did not produce manifests.")
            matrix_member = file_member(matrix_path)
            matrix_member["matrix_id"] = payload["matrix_id"]
            bundle = finalize_history_bundle(
                {
                    "schema_version": BUNDLE_SCHEMA_VERSION,
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
                    "wheel": file_member(published_wheel),
                    "environments": environments,
                    "matrix": matrix_member,
                    "limitations": limitations(),
                },
                directory=output,
            )
            write_history_bundle(output / "bundle.json", bundle)

    for observation in observations:
        print(
            f"{observation['observed']:4} {observation['case_id']} "
            f"openai-agents={observation['sdk_version']} expected={observation['expected']}"
        )
    print(f"matrix: {output / 'matrix.json'}")
    if source is not None:
        print(f"bundle: {output / 'bundle.json'}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the pinned AgentRunProof history matrix.")
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_BUILD)
    parser.add_argument(
        "--canonical",
        action="store_true",
        help="Use hash-locked Linux wheels and publish a clean-source bundle marker.",
    )
    parser.add_argument(
        "--source-commit",
        help="Expected clean source commit; defaults to HEAD in canonical mode.",
    )
    return parser


def _build_wheel(distribution_dir: Path) -> Path:
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
        timeout=300,
    )
    wheels = sorted(distribution_dir.glob("agentrunproof-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one AgentRunProof wheel, found {len(wheels)}.")
    return wheels[0]


def _prepare_environment(
    sdk_version: str,
    *,
    wheel: Path,
    scratch: Path,
    canonical: bool,
) -> PreparedEnvironment:
    environment = scratch / "venvs" / sdk_version
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    manifest: dict[str, JsonValue] | None = None
    if canonical:
        lock = LOCKS / f"openai-agents-{sdk_version}-linux-py312.txt"
        wheelhouse = scratch / "wheelhouse" / sdk_version
        wheelhouse.mkdir(parents=True)
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
            timeout=300,
        )
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
            timeout=300,
        )
        artifacts = sorted(
            (_wheel_artifact(path) for path in wheelhouse.glob("*.whl")),
            key=lambda item: str(item["distribution"]),
        )
        manifest = {
            "schema_version": ENVIRONMENT_SCHEMA_VERSION,
            "sdk_version": sdk_version,
            "python": _python_version(python),
            "pip_version": _pip_version(python),
            "dependency_metadata_bypassed": sdk_version != "0.20.0",
            "lock_sha256": _lock_digest(lock),
            "artifacts": artifacts,
        }
    else:
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                f"openai-agents=={sdk_version}",
            ],
            cwd=ROOT,
            timeout=300,
        )
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--force-reinstall",
            "--no-deps",
            str(wheel),
        ],
        cwd=ROOT,
        timeout=300,
    )
    return PreparedEnvironment(python=python, manifest=manifest)


def _execute(run: MatrixRun, python: Path, *, scratch: Path) -> dict[str, Any]:
    worker_cwd = Path(tempfile.mkdtemp(prefix=f"worker-{run.sdk_version}-", dir=scratch))
    completed = subprocess.run(
        [str(python), "-I", "-c", _NETWORK_GUARDED_WORKER, run.case_id],
        cwd=worker_cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
        timeout=WORKER_TIMEOUT_SECONDS,
    )
    if completed.stderr:
        raise RuntimeError(f"History worker wrote stderr for {run.sdk_version}: {completed.stderr}")
    try:
        worker = parse_history_worker_json(completed.stdout)
    except ValueError as error:
        raise RuntimeError(
            f"History worker did not return strict JSON for {run.sdk_version}."
        ) from error
    result = worker.get("result")
    observed = result.get("overall_status") if isinstance(result, dict) else "ERROR"
    return {
        "case_id": run.case_id,
        "sdk_version": run.sdk_version,
        "expected": run.expected,
        "observed": observed,
        "worker_exit": completed.returncode,
        "worker": worker,
    }


def _canonical_source(expected_commit: str | None) -> dict[str, JsonValue]:
    commit = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    index = _git("ls-files", "-v")
    if not commit or not tree or status is None or index is None:
        raise RuntimeError("Canonical evidence requires a readable Git worktree.")
    if expected_commit is not None and commit != expected_commit:
        raise RuntimeError(f"HEAD {commit} does not match --source-commit {expected_commit}.")
    hidden_flags = [
        line for line in index.splitlines() if line and (line[0].islower() or line[0] == "S")
    ]
    if status or hidden_flags:
        raise RuntimeError("Canonical evidence requires a clean worktree and clean index flags.")
    return {
        "commit": commit,
        "tree": tree,
        "clean": True,
        "index_flags_clean": True,
    }


def _assert_source_stable(source: dict[str, JsonValue]) -> None:
    observed = _canonical_source(_require_string(source["commit"]))
    if observed != source:
        raise RuntimeError("Source provenance changed while the history matrix was running.")


def _validate_canonical_host() -> None:
    if (
        platform.python_implementation() != "CPython"
        or sys.version_info[:2] != (3, 12)
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise RuntimeError("Canonical history evidence requires Linux x86_64 CPython 3.12.")


def _wheel_artifact(path: Path) -> dict[str, JsonValue]:
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


def _python_version(python: Path) -> str:
    return _capture([str(python), "-I", "-c", "import platform;print(platform.python_version())"])


def _pip_version(python: Path) -> str:
    output = _capture([str(python), "-m", "pip", "--version"])
    fields = output.split()
    if len(fields) < 2 or fields[0] != "pip":
        raise RuntimeError(f"Cannot parse pip version: {output!r}.")
    return fields[1]


def _capture(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError(f"Command did not produce clean output: {command!r}.")
    return completed.stdout.strip()


def _run(command: list[str], *, cwd: Path, timeout: int) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit {completed.returncode}: {command!r}")


def _git(*arguments: str) -> str | None:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _atomic_text(path: Path, text: str) -> None:
    _atomic_bytes(path, text.encode("utf-8"))


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


def _require_string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise RuntimeError("Expected a string provenance value.")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
