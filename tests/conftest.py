from __future__ import annotations

from collections.abc import Callable

import pytest
from agents import Agent

from agentrunproof.model import DeterministicModel, assistant_message
from agentrunproof.scenario import RunVariant, Scenario, ScenarioCase
from agentrunproof.session import RecordingSession


@pytest.fixture
def scenario_factory() -> Callable[..., Scenario]:
    def make(
        *,
        non_streaming_output: str = "done",
        streaming_output: str = "done",
        public_payloads: bool = True,
        invariants: tuple[str, ...] = (
            "execution_outcome",
            "stream_parity",
            "model_script_consumed",
        ),
        expected_tool_counts: dict[str, int] | None = None,
    ) -> Scenario:
        def build(variant: RunVariant) -> ScenarioCase:
            output = (
                non_streaming_output if variant is RunVariant.NON_STREAMING else streaming_output
            )
            model = DeterministicModel([[assistant_message(output)]])
            return ScenarioCase(
                agent=Agent(name="fixture agent", model=model),
                input="public fixture input" if public_payloads else "SECRET-FIXTURE-INPUT",
                model=model,
                session=RecordingSession(),
                tool_counts={},
            )

        return Scenario(
            scenario_id="fixture-scenario",
            revision=1,
            description="A deterministic test fixture.",
            variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
            invariants=invariants,
            factory=build,
            expected_tool_counts=expected_tool_counts or {},
            public_payloads=public_payloads,
        )

    return make
