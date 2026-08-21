from __future__ import annotations

import argparse
import re
from pathlib import Path

import agentrunproof
import agentrunproof.current as current
from agentrunproof.builtins import SCENARIOS
from agentrunproof.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]


def _reference_names(path: str, marker: str) -> list[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    start = f"<!-- reference-table:{marker}:start -->"
    end = f"<!-- reference-table:{marker}:end -->"
    assert text.count(start) == 1
    assert text.count(end) == 1
    section = text.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]
    names = re.findall(r"^\| `([^`]+)` \|", section, flags=re.MULTILINE)
    assert names
    assert len(names) == len(set(names))
    return names


def _cli_commands() -> list[str]:
    parser = build_parser()
    subparsers = [
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(subparsers) == 1
    return list(subparsers[0].choices)


def test_api_reference_matches_exported_surfaces() -> None:
    assert _reference_names("docs/api-reference.md", "agentrunproof") == agentrunproof.__all__
    assert _reference_names("docs/api-reference.md", "agentrunproof.current") == current.__all__


def test_cli_reference_matches_commands_and_builtin_scenarios() -> None:
    assert _reference_names("docs/cli-reference.md", "commands") == _cli_commands()
    assert _reference_names("docs/cli-reference.md", "scenarios") == sorted(SCENARIOS)


def test_documentation_entry_points_link_to_the_reference() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "docs/README.md" in readme
    assert "docs/api-reference.md" in readme
    assert "docs/cli-reference.md" in readme
    assert "](api-reference.md)" in index
    assert "](cli-reference.md)" in index
    assert "/blob/main/docs/README.md" in pyproject


def test_api_reference_tracks_the_package_version() -> None:
    reference = (ROOT / "docs/api-reference.md").read_text(encoding="utf-8")

    assert f'__version__: str = "{agentrunproof.__version__}"' in reference
