from __future__ import annotations

import json
from pathlib import Path

from agentrunproof.cli import main


def test_cli_lists_and_runs_builtin_scenario(tmp_path: Path, capsys) -> None:
    assert main(["list-scenarios"]) == 0
    listed = capsys.readouterr().out
    assert "basic-tool-session-parity" in listed
    assert "runstate-sibling-approval-isolation" in listed

    certificate = tmp_path / "certificate.json"
    assert (
        main(
            [
                "probe",
                "basic-tool-session-parity",
                "--certificate",
                str(certificate),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "PASS basic-tool-session-parity" in output
    assert certificate.exists()
    assert main(["check-certificate", str(certificate)]) == 0
    assert "VALID sha256:" in capsys.readouterr().out


def test_cli_emits_a_checkable_state_fork_certificate(tmp_path: Path, capsys) -> None:
    certificate = tmp_path / "state-fork.json"
    exit_code = main(
        [
            "probe",
            "runstate-sibling-approval-isolation",
            "--certificate",
            str(certificate),
        ]
    )
    payload = json.loads(certificate.read_text(encoding="utf-8"))
    expected_exit = 0 if payload["overall_status"] == "PASS" else 1

    assert exit_code == expected_exit
    assert payload["scenario"]["id"] == "runstate-sibling-approval-isolation"
    assert "state_fork_isolation" in payload["scenario"]["requested_invariants"]
    assert f"{payload['overall_status']} runstate-sibling-approval-isolation" in (
        capsys.readouterr().out
    )
    assert main(["check-certificate", str(certificate)]) == 0
    assert "VALID sha256:" in capsys.readouterr().out


def test_cli_rejects_tampered_certificate(tmp_path: Path, capsys) -> None:
    certificate = tmp_path / "certificate.json"
    assert main(["probe", "basic-tool-session-parity", "--certificate", str(certificate)]) == 0
    capsys.readouterr()

    payload = json.loads(certificate.read_text(encoding="utf-8"))
    payload["overall_status"] = "FAIL"
    certificate.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["check-certificate", str(certificate)]) == 2
    assert "certificate_id does not match" in capsys.readouterr().err


def test_cli_rejects_non_finite_json_without_a_traceback(tmp_path: Path, capsys) -> None:
    certificate = tmp_path / "non-finite.json"
    certificate.write_text('{"value": NaN}\n', encoding="utf-8")

    assert main(["check-certificate", str(certificate)]) == 2
    assert "Non-finite JSON number is forbidden" in capsys.readouterr().err


def test_cli_rejects_duplicate_json_keys_without_a_traceback(tmp_path: Path, capsys) -> None:
    certificate = tmp_path / "duplicate-key.json"
    certificate.write_text('{"overall_status":"FAIL","overall_status":"PASS"}\n')

    assert main(["check-certificate", str(certificate)]) == 2
    assert "Duplicate JSON object key" in capsys.readouterr().err


def test_cli_checks_a_strict_history_matrix(tmp_path: Path, capsys, monkeypatch) -> None:
    path = tmp_path / "matrix.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "agentrunproof.cli.load_history_matrix",
        lambda candidate: {"matrix_id": f"sha256:{'a' * 64}"} if candidate == path else None,
    )

    assert main(["check-history-matrix", str(path)]) == 0
    assert "history-matrix" in capsys.readouterr().out


def test_cli_checks_a_history_bundle(tmp_path: Path, capsys, monkeypatch) -> None:
    path = tmp_path / "bundle.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "agentrunproof.cli.load_history_bundle",
        lambda candidate: {"bundle_id": f"sha256:{'b' * 64}"} if candidate == path else None,
    )

    assert main(["check-history-bundle", str(path)]) == 0
    assert "history-bundle" in capsys.readouterr().out
