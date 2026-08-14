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
import tempfile
import urllib.parse
import venv
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from agentrunproof._canonical import JsonValue, sha256_hex
from agentrunproof.certificate import CertificateError, certificate_json, validate_certificate
from agentrunproof.current.bundle import (
    BUNDLE_SCHEMA_VERSION,
    ENVIRONMENT_ID,
    finalize_current_bundle,
    finalize_current_certificate,
    limitations,
    write_current_bundle,
)
from agentrunproof.history.bundle import (
    file_member,
    finalize_environment,
    load_history_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD = ROOT / "build" / "current"
LOCK = ROOT / "requirements" / "history" / "openai-agents-0.20.0-linux-py312.txt"
HISTORY_BUNDLE = ROOT / "evidence" / "history" / "v1" / "bundle.json"
WORKER_TIMEOUT_SECONDS = 120
_WHEEL_METADATA = re.compile(r"^[^/]+\.dist-info/METADATA$")

_NETWORK_GUARDED_WORKER = """
import asyncio
import socket
import sys
from pathlib import Path

def _deny_network(*_args, **_kwargs):
    raise RuntimeError("AgentRunProof current evidence worker network access is disabled")

class _DeniedSocket(socket.socket):
    def connect(self, *_args, **_kwargs):
        return _deny_network()

    def connect_ex(self, *_args, **_kwargs):
        return _deny_network()

socket.socket = _DeniedSocket
socket.create_connection = _deny_network
socket.create_server = _deny_network

import agentrunproof
module = Path(agentrunproof.__file__).resolve()
if "site-packages" not in module.parts:
    raise RuntimeError(f"Current evidence worker did not import the installed wheel: {module}")

from agentrunproof.builtins import get_scenario
from agentrunproof.certificate import build_certificate, certificate_json
from agentrunproof.engine import run_scenario

scenario = get_scenario("runstate-sibling-approval-isolation")
proof = asyncio.run(run_scenario(scenario))
certificate = build_certificate(proof)
sys.stdout.write(certificate_json(certificate))
raise SystemExit(0 if proof.status == "PASS" else 1)
""".strip()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.canonical:
        raise RuntimeError("Current public evidence generation requires --canonical.")
    _validate_canonical_host()
    source = _canonical_source(args.source_commit)
    output = args.output_directory.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("Canonical output directory must start empty.")
    output.mkdir(parents=True, exist_ok=True)

    canonical_environment = _canonical_environment()
    with tempfile.TemporaryDirectory(prefix="agentrunproof-current-") as scratch_name:
        scratch = Path(scratch_name)
        wheel = _build_wheel(scratch / "dist")
        python, environment = _prepare_environment(
            wheel=wheel,
            scratch=scratch,
            canonical_environment=canonical_environment,
        )
        unbound_certificate = _execute(python, scratch=scratch)
        _assert_source_stable(source)
        source_commit = source.get("commit")
        if not isinstance(source_commit, str):
            raise RuntimeError("Canonical source commit is missing.")
        certificate = finalize_current_certificate(
            unbound_certificate,
            source_commit=source_commit,
        )
        _assert_source_stable(source)

        published_wheel = output / wheel.name
        _atomic_bytes(published_wheel, wheel.read_bytes())
        certificate_path = output / "certificate.json"
        _atomic_text(certificate_path, certificate_json(certificate))

        certificate_member = file_member(certificate_path)
        certificate_member["certificate_id"] = certificate["certificate_id"]
        bundle = finalize_current_bundle(
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
                "environment": environment,
                "certificate": certificate_member,
                "limitations": limitations(),
            },
            directory=output,
        )
        _assert_source_stable(source)
        write_current_bundle(output / "bundle.json", bundle)

    print(f"certificate: {output / 'certificate.json'}")
    print(f"bundle: {output / 'bundle.json'}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the canonical current sibling-isolation certificate."
    )
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_BUILD)
    parser.add_argument(
        "--canonical",
        action="store_true",
        help="Use the hash-locked Linux environment and publish a clean-source bundle.",
    )
    parser.add_argument(
        "--source-commit",
        help="Expected clean source commit; defaults to HEAD.",
    )
    return parser


def _canonical_environment() -> dict[str, JsonValue]:
    history = load_history_bundle(HISTORY_BUNDLE)
    environments = history.get("environments")
    if not isinstance(environments, list):
        raise RuntimeError("The canonical history bundle has no environments.")
    matches = [
        cast(dict[str, JsonValue], item)
        for item in environments
        if isinstance(item, dict) and item.get("environment_id") == ENVIRONMENT_ID
    ]
    if len(matches) != 1 or matches[0].get("sdk_version") != "0.20.0":
        raise RuntimeError("The canonical 0.20.0 history environment is missing.")
    return matches[0]


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
    *,
    wheel: Path,
    scratch: Path,
    canonical_environment: dict[str, JsonValue],
) -> tuple[Path, dict[str, JsonValue]]:
    environment = scratch / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    wheelhouse = scratch / "wheelhouse"
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
            str(LOCK),
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
            str(LOCK),
        ],
        cwd=ROOT,
        timeout=300,
    )
    artifacts = sorted(
        (_wheel_artifact(path) for path in wheelhouse.glob("*.whl")),
        key=lambda item: str(item["distribution"]),
    )
    observed_environment = finalize_environment(
        {
            "schema_version": "agentrunproof.history-environment/v1",
            "sdk_version": "0.20.0",
            "python": _python_version(python),
            "pip_version": _pip_version(python),
            "dependency_metadata_bypassed": False,
            "lock_sha256": _lock_digest(LOCK),
            "artifacts": artifacts,
        }
    )
    if observed_environment != canonical_environment:
        raise RuntimeError("Observed environment does not match the canonical 0.20.0 closure.")
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
    _assert_installed_wheel(python, wheel=wheel)
    return python, observed_environment


def _execute(python: Path, *, scratch: Path) -> dict[str, JsonValue]:
    worker_cwd = Path(tempfile.mkdtemp(prefix="worker-", dir=scratch))
    completed = subprocess.run(
        [str(python), "-I", "-c", _NETWORK_GUARDED_WORKER],
        cwd=worker_cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
        timeout=WORKER_TIMEOUT_SECONDS,
    )
    if completed.stderr:
        raise RuntimeError(f"Current evidence worker wrote stderr: {completed.stderr}")
    if completed.returncode != 1:
        raise RuntimeError(
            f"Current evidence worker must reproduce the pinned failure (exit 1), got {completed.returncode}."
        )
    try:
        value = json.loads(
            completed.stdout,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
        return validate_certificate(value)
    except (CertificateError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(
            "Current evidence worker did not return a strict certificate."
        ) from error


def _assert_installed_wheel(python: Path, *, wheel: Path) -> None:
    probe = _capture(
        [
            str(python),
            "-I",
            "-c",
            """
import importlib.metadata
import json
from pathlib import Path
import agentrunproof

distribution = importlib.metadata.distribution("agentrunproof")
direct_url = distribution.read_text("direct_url.json")
print(json.dumps({
    "module": str(Path(agentrunproof.__file__).resolve()),
    "direct_url": json.loads(direct_url) if direct_url is not None else None,
}, sort_keys=True))
""".strip(),
        ]
    )
    try:
        payload = json.loads(probe)
        module = Path(payload["module"])
        direct_url = payload["direct_url"]
        archive_hash = direct_url["archive_info"]["hashes"]["sha256"]
        archive_path = Path(urllib.parse.unquote(urllib.parse.urlparse(direct_url["url"]).path))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("Cannot verify the installed AgentRunProof wheel origin.") from error
    environment = python.parent.parent
    try:
        module.relative_to(environment)
    except ValueError as error:
        raise RuntimeError("AgentRunProof was not imported from the fresh environment.") from error
    if "site-packages" not in module.parts:
        raise RuntimeError("AgentRunProof was not imported from site-packages.")
    if archive_path.name != wheel.name or archive_hash != _sha256_file(wheel):
        raise RuntimeError("Installed AgentRunProof does not match the bundle wheel.")


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
    commit = source.get("commit")
    if not isinstance(commit, str):
        raise RuntimeError("Source commit is missing.")
    if _canonical_source(commit) != source:
        raise RuntimeError("Source provenance changed while current evidence was running.")


def _validate_canonical_host() -> None:
    if (
        platform.python_implementation() != "CPython"
        or platform.python_version() != "3.12.13"
        or platform.system() != "Linux"
        or platform.machine() != "x86_64"
    ):
        raise RuntimeError("Canonical current evidence requires Linux x86_64 CPython 3.12.13.")


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


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key!r}.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is forbidden: {value}.")


if __name__ == "__main__":
    raise SystemExit(main())
