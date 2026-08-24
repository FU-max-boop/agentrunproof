from __future__ import annotations

import json
from pathlib import Path

from agentrunproof.cli import main


def test_cli_lists_and_runs_builtin_scenario(tmp_path: Path, capsys) -> None:
    assert main(["list-scenarios"]) == 0
    listed = capsys.readouterr().out
    assert "basic-tool-session-parity" in listed
    assert "handoff-session-filtered-view-parity" in listed
    assert "runstate-recursive-agent-tool-approval-serialization" in listed
    assert "runstate-recursive-agent-tool-approval-routing" in listed
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


def test_cli_emits_a_checkable_handoff_session_certificate(tmp_path: Path, capsys) -> None:
    certificate = tmp_path / "handoff-session.json"

    assert (
        main(
            [
                "probe",
                "handoff-session-filtered-view-parity",
                "--certificate",
                str(certificate),
            ]
        )
        == 0
    )
    payload = json.loads(certificate.read_text(encoding="utf-8"))
    assert payload["scenario"]["id"] == "handoff-session-filtered-view-parity"
    assert payload["overall_status"] == "PASS"
    assert payload["observations"]["non_streaming"]["phases"][0]["probes_after"][
        "guardrail_events"
    ][-1] == {
        "stage": "output",
        "agent": "Domain Specialist",
        "payload": "resolved-alpha",
    }
    assert "PASS handoff-session-filtered-view-parity" in capsys.readouterr().out
    assert main(["check-certificate", str(certificate)]) == 0
    assert "VALID sha256:" in capsys.readouterr().out


def test_cli_emits_a_checkable_recursive_approval_certificate(tmp_path: Path, capsys) -> None:
    certificate = tmp_path / "recursive-approval.json"
    exit_code = main(
        [
            "probe",
            "runstate-recursive-agent-tool-approval-routing",
            "--certificate",
            str(certificate),
        ]
    )
    payload = json.loads(certificate.read_text(encoding="utf-8"))
    expected_exit = 0 if payload["overall_status"] == "PASS" else 1

    assert exit_code == expected_exit
    assert payload["scenario"]["id"] == "runstate-recursive-agent-tool-approval-routing"
    assert "recursive_approval_routing" in payload["scenario"]["requested_invariants"]
    contracts = payload["scenario"]["phase_contracts"]["non_streaming"]
    assert contracts[1]["save_sibling_state"] is True
    assert contracts[2]["saved_sibling_from"] == "untouched-sibling"
    assert f"{payload['overall_status']} runstate-recursive-agent-tool-approval-routing" in (
        capsys.readouterr().out
    )
    assert main(["check-certificate", str(certificate)]) == 0
    assert "VALID sha256:" in capsys.readouterr().out


def test_cli_emits_a_checkable_recursive_serialization_certificate(tmp_path: Path, capsys) -> None:
    certificate = tmp_path / "recursive-serialization.json"
    exit_code = main(
        [
            "probe",
            "runstate-recursive-agent-tool-approval-serialization",
            "--certificate",
            str(certificate),
        ]
    )
    payload = json.loads(certificate.read_text(encoding="utf-8"))
    expected_exit = 0 if payload["overall_status"] == "PASS" else 1

    assert exit_code == expected_exit
    assert payload["scenario"]["id"] == "runstate-recursive-agent-tool-approval-serialization"
    contracts = payload["scenario"]["phase_contracts"]["non_streaming"]
    assert contracts[1]["json_round_trip"] is True
    assert contracts[1]["decisions"] == [
        {
            "action": "approve",
            "call_id_sha256": ("18a3037806c47a97bbcc4dda440b96c9213b8ff2114345156c2bde1e44f226ec"),
            "rejection_message": None,
        }
    ]
    assert (
        f"{payload['overall_status']} "
        "runstate-recursive-agent-tool-approval-serialization" in capsys.readouterr().out
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
