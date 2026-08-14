from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_script() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_history_matrix.py"
    spec = importlib.util.spec_from_file_location("agentrunproof_history_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_main_finalizes_validates_and_atomically_writes_the_matrix(
    tmp_path: Path, monkeypatch
) -> None:
    script = _load_script()
    wheel = tmp_path / "agentrunproof-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"public synthetic wheel")
    observations: list[dict[str, Any]] = []
    finalized: list[dict[str, Any]] = []
    validated: list[dict[str, Any]] = []

    def fake_execute(run: Any, candidate: Path, *, scratch: Path) -> dict[str, Any]:
        assert candidate == wheel
        assert scratch.is_dir()
        observation = {
            "case_id": run.case_id,
            "sdk_version": run.sdk_version,
            "expected": run.expected,
            "observed": run.expected,
            "worker_exit": 0 if run.expected == "PASS" else 1,
            "worker": {},
        }
        observations.append(observation)
        return observation

    def fake_finalize(payload: dict[str, Any]) -> dict[str, Any]:
        finalized.append(payload)
        return {**payload, "matrix_id": f"sha256:{'a' * 64}"}

    output = tmp_path / "evidence"
    monkeypatch.setattr(script, "_build_wheel", lambda _directory: wheel)
    monkeypatch.setattr(
        script,
        "_prepare_environment",
        lambda _version, **_kwargs: script.PreparedEnvironment(
            python=wheel,
            manifest=None,
        ),
    )
    monkeypatch.setattr(script, "_execute", fake_execute)
    monkeypatch.setattr(script, "finalize_history_matrix", fake_finalize)
    monkeypatch.setattr(script, "validate_history_matrix", lambda value: validated.append(value))
    monkeypatch.setattr(
        script,
        "history_matrix_json",
        lambda value: json.dumps(value, sort_keys=True) + "\n",
    )

    assert script.main(["--output-directory", str(output)]) == 0
    assert len(observations) == 6
    assert finalized[0]["expectations_matched"] is True
    assert validated == [{**finalized[0], "matrix_id": f"sha256:{'a' * 64}"}]
    written = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    assert written["matrix_id"] == f"sha256:{'a' * 64}"


def test_canonical_source_binds_clean_commit_tree_and_index(monkeypatch) -> None:
    script = _load_script()
    commit = "1" * 40
    tree = "2" * 40
    responses = {
        ("rev-parse", "HEAD"): commit,
        ("rev-parse", "HEAD^{tree}"): tree,
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
        ("ls-files", "-v"): "H README.md",
    }
    monkeypatch.setattr(script, "_git", lambda *arguments: responses.get(arguments))

    assert script._canonical_source(commit) == {
        "commit": commit,
        "tree": tree,
        "clean": True,
        "index_flags_clean": True,
    }


@pytest.mark.parametrize(
    ("status", "index"),
    [
        (" M README.md", "H README.md"),
        ("", "h README.md"),
        ("", "S README.md"),
    ],
)
def test_canonical_source_rejects_dirty_or_hidden_index_state(
    monkeypatch, status: str, index: str
) -> None:
    script = _load_script()
    responses = {
        ("rev-parse", "HEAD"): "1" * 40,
        ("rev-parse", "HEAD^{tree}"): "2" * 40,
        ("status", "--porcelain=v1", "--untracked-files=all"): status,
        ("ls-files", "-v"): index,
    }
    monkeypatch.setattr(script, "_git", lambda *arguments: responses.get(arguments))

    with pytest.raises(RuntimeError, match="clean worktree and clean index flags"):
        script._canonical_source("1" * 40)


def test_canonical_run_refuses_to_replace_an_existing_payload(tmp_path: Path, monkeypatch) -> None:
    script = _load_script()
    output = tmp_path / "evidence"
    output.mkdir()
    (output / "matrix.json").write_text("existing\n", encoding="utf-8")
    monkeypatch.setattr(script, "_validate_canonical_host", lambda: None)

    with pytest.raises(RuntimeError, match="must start empty"):
        script.main(["--canonical", "--output-directory", str(output)])
