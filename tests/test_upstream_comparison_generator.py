from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agentrunproof.current.comparison import (
    RECURSIVE_CASE_ID,
    TOP_LEVEL_CASE_ID,
    stable_worker_argv,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_upstream_comparison.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_generator_uses_installed_wheels_and_stable_isolated_argv() -> None:
    module = _script_module()
    source = SCRIPT.read_text(encoding="utf-8")

    assert stable_worker_argv(TOP_LEVEL_CASE_ID) == [
        "python",
        "-I",
        "-m",
        "agentrunproof.current.worker",
        TOP_LEVEL_CASE_ID,
    ]
    assert stable_worker_argv(RECURSIVE_CASE_ID)[1:4] == [
        "-I",
        "-m",
        "agentrunproof.current.worker",
    ]
    assert "PYTHONPATH" not in source
    assert "sys.path.insert" not in source
    assert '"site-packages" not in path.parts' in source
    assert source.count("upstream_sources[target_id],\n                target_id=target_id,") == 2
    assert source.rindex("upstream_sources[target_id],") < source.index(
        'write_upstream_comparison(output / "bundle.json", bundle)'
    )
    assert "The final bundle is the commit marker" in source
    assert module.WORKER_TIMEOUT_SECONDS == 120


def test_source_wheel_direct_url_is_bound_to_exact_file_and_hash(tmp_path: Path) -> None:
    module = _script_module()
    wheel = tmp_path / "openai_agents-0.20.0-py3-none-any.whl"
    wheel.write_bytes(b"source-built upstream wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    direct_url = {
        "url": wheel.as_uri(),
        "archive_info": {"hashes": {"sha256": digest}},
    }

    assert module._normalize_direct_url(direct_url, expected_wheel=wheel) == {
        "url_scheme": "file",
        "url_basename": wheel.name,
        "archive_sha256": digest,
    }

    replacement = tmp_path / "replacement" / wheel.name
    replacement.parent.mkdir()
    replacement.write_bytes(wheel.read_bytes())
    with pytest.raises(RuntimeError, match="does not point"):
        module._normalize_direct_url(direct_url, expected_wheel=replacement)

    forged = json.loads(json.dumps(direct_url))
    forged["archive_info"]["hashes"]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="hash"):
        module._normalize_direct_url(forged, expected_wheel=wheel)


def test_sdk_origin_rejects_pypi_substitution_for_source_target(tmp_path: Path) -> None:
    module = _script_module()
    wheel = tmp_path / "openai_agents-0.20.0-py3-none-any.whl"
    wheel.write_bytes(b"source-built upstream wheel")
    probe = {
        "openai_agents": {
            "version": "0.20.0",
            "direct_url": None,
        }
    }

    with pytest.raises(RuntimeError, match="direct_url"):
        module._validate_sdk_origin(
            probe,
            environment=tmp_path,
            target_id=module.UPSTREAM_TARGET_ID,
            source_wheel=wheel,
        )


def test_upstream_lock_digest_is_frozen_and_complete() -> None:
    module = _script_module()

    assert module._lock_digest(module.UPSTREAM_LOCK) == module.UPSTREAM_LOCK_SHA256
    locked = [
        line
        for line in module.UPSTREAM_LOCK.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(locked) == 39
    assert all(" --hash=sha256:" in line for line in locked)
    assert not any(line.startswith("openai-agents==") for line in locked)

    build_locked = [
        line
        for line in module.BUILD_LOCK.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(build_locked) == 7
    assert all(" --hash=sha256:" in line for line in build_locked)
    assert any(line.startswith("build==1.5.0 ") for line in build_locked)
    assert any(line.startswith("hatchling==1.26.3 ") for line in build_locked)


def test_generator_parses_extensible_source_targets_and_runs(tmp_path: Path) -> None:
    module = _script_module()
    args = module._parser().parse_args(
        [
            "--canonical",
            "--upstream-checkout",
            str(tmp_path / "baseline"),
            "--additional-upstream-checkout",
            f"openai-agents-python-50d65f65={tmp_path / 'fixed'}",
            "--additional-run",
            (
                "fixed-recursive,openai-agents-python-50d65f65,"
                "runstate-recursive-agent-tool-approval-routing,PASS,fixed.json"
            ),
            "--additional-run",
            (
                "serialized-baseline,openai-agents-python-0b93ce8,"
                "runstate-recursive-agent-tool-approval-serialization,FAIL,serialized.json"
            ),
        ]
    )

    checkouts = module._source_checkouts(args)
    requests = module._run_requests(args, source_target_ids=list(checkouts))
    assert list(checkouts) == [
        "openai-agents-python-0b93ce8",
        "openai-agents-python-50d65f65",
    ]
    assert [request.run_id for request in requests][-2:] == [
        "serialized-baseline",
        "fixed-recursive",
    ]
    assert [request.expected_exit for request in requests] == [1, 0, 1, 1, 0]


def test_generator_rejects_unobserved_or_duplicate_additions(tmp_path: Path) -> None:
    module = _script_module()
    args = module._parser().parse_args(
        [
            "--canonical",
            "--upstream-checkout",
            str(tmp_path / "baseline"),
            "--additional-upstream-checkout",
            f"openai-agents-python-50d65f65={tmp_path / 'fixed'}",
        ]
    )
    checkouts = module._source_checkouts(args)
    with pytest.raises(RuntimeError, match="at least one run"):
        module._run_requests(args, source_target_ids=list(checkouts))


def test_ci_can_generate_the_first_canonical_candidate_from_an_exact_target() -> None:
    complete_workflow = WORKFLOW.read_text(encoding="utf-8")
    workflow = complete_workflow.split("\n  upstream-comparison:\n", 1)[1]

    assert "upstream_target_commit:" in complete_workflow
    assert "INPUT_UPSTREAM_TARGET_COMMIT: ${{ inputs.upstream_target_commit }}" in workflow
    assert 'target_commit="$INPUT_UPSTREAM_TARGET_COMMIT"' in workflow
    assert 'test "$GITHUB_REF" = refs/heads/main' in workflow
    assert '[[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]' in workflow
    assert "runstate-recursive-agent-tool-approval-routing,PASS" in workflow
    assert "runstate-recursive-agent-tool-approval-serialization,PASS" in workflow
    assert "candidate-top-level" not in workflow
    assert "python -m agentrunproof check-upstream-bundle" in workflow
    assert 'committed_bundle="evidence/upstream-comparison/v1/bundle.json"' in workflow
    assert "find evidence/upstream " not in workflow
    assert 'git merge-base --is-ancestor "$source_commit" "$evidence_parent"' in workflow
    assert 'git diff --name-only "$evidence_parent..$evidence_commit"' in workflow
    assert 'test "${evidence_parent_fields[1]}" = "$source_commit"' not in workflow


def test_replaceable_source_target_binds_clean_git_provenance(tmp_path: Path) -> None:
    module = _script_module()
    checkout = tmp_path / "upstream"
    checkout.mkdir()

    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    git("init", "--quiet")
    git("config", "user.name", "AgentRunProof test")
    git("config", "user.email", "test@agentrunproof.invalid")
    git("remote", "add", "origin", "https://github.com/openai/openai-agents-python.git")
    (checkout / "README.md").write_text("base\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "--quiet", "-m", "base")
    parent = git("rev-parse", "HEAD")
    (checkout / "pyproject.toml").write_text("[project]\nname='openai-agents'\n", encoding="utf-8")
    (checkout / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    git("add", "pyproject.toml", "uv.lock")
    git("commit", "--quiet", "-m", "candidate")
    commit = git("rev-parse", "HEAD")
    target_id = f"openai-agents-python-{commit[:8]}"
    module.UPSTREAM_PYPROJECT_SHA256 = hashlib.sha256(
        (checkout / "pyproject.toml").read_bytes()
    ).hexdigest()
    module.UPSTREAM_UV_LOCK_SHA256 = hashlib.sha256((checkout / "uv.lock").read_bytes()).hexdigest()

    source = module._upstream_source(checkout, target_id=target_id)
    assert source["commit"] == commit
    assert source["parent"] == parent
    assert source["tree"] == git("rev-parse", "HEAD^{tree}")
    assert (
        source["pyproject_sha256"]
        == hashlib.sha256((checkout / "pyproject.toml").read_bytes()).hexdigest()
    )

    (checkout / "uv.lock").write_text("modified\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="clean upstream checkout"):
        module._upstream_source(checkout, target_id=target_id)


def _script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_upstream_comparison", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
