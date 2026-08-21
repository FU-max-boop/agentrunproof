from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_packaged_wheel_matrix_covers_every_supported_python_and_sdk() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]' in workflow
    assert 'sdk-version: ["0.20.0", "0.21.0", "0.22.0"]' in workflow
    assert "Build the wheel for this Python cell" in workflow
    assert "Run tests against the installed wheel" in workflow
