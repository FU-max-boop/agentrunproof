from __future__ import annotations

import importlib.metadata

import agentrunproof


def test_distribution_and_package_versions_match() -> None:
    assert importlib.metadata.version("agentrunproof") == agentrunproof.__version__
    assert agentrunproof.__version__ == "0.1.0"


def test_documented_scenario_building_surface_is_public() -> None:
    for name in (
        "DeterministicModel",
        "RecordingSession",
        "Scenario",
        "ScenarioCase",
        "ScenarioPhase",
        "ScenarioPlan",
        "StateProbe",
        "build_certificate",
        "run_scenario",
        "write_certificate",
    ):
        assert getattr(agentrunproof, name)
