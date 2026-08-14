from __future__ import annotations

from agents import Agent, function_tool

from .model import DeterministicModel, assistant_message, function_call
from .scenario import RunVariant, Scenario, ScenarioCase
from .session import RecordingSession


def _basic_tool_session_case(variant: RunVariant) -> ScenarioCase:
    del variant
    tool_counts = {"lookup_value": 0}

    @function_tool
    def lookup_value(key: str) -> str:
        """Return one deterministic value for a public synthetic key."""

        tool_counts["lookup_value"] += 1
        return "42" if key == "alpha" else "unknown"

    model = DeterministicModel(
        [
            [
                function_call(
                    "lookup_value",
                    {"key": "alpha"},
                    call_id="agentrunproof-call-1",
                )
            ],
            [assistant_message("value:42")],
        ]
    )
    agent = Agent(
        name="AgentRunProof basic tool agent",
        instructions="Use the deterministic lookup tool, then return its value.",
        model=model,
        tools=[lookup_value],
    )
    return ScenarioCase(
        agent=agent,
        input="Look up the public synthetic key alpha.",
        model=model,
        session=RecordingSession(),
        max_turns=3,
        tool_counts=tool_counts,
    )


BASIC_TOOL_SESSION_PARITY = Scenario(
    scenario_id="basic-tool-session-parity",
    revision=1,
    description="One function tool call and final output remain coherent across runner modes.",
    variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
    invariants=(
        "execution_outcome",
        "stream_parity",
        "tool_linkage",
        "exactly_once",
        "model_script_consumed",
    ),
    factory=_basic_tool_session_case,
    expected_tool_counts={"lookup_value": 1},
    public_payloads=True,
)


SCENARIOS: dict[str, Scenario] = {
    BASIC_TOOL_SESSION_PARITY.scenario_id: BASIC_TOOL_SESSION_PARITY,
}


def get_scenario(scenario_id: str) -> Scenario:
    try:
        return SCENARIOS[scenario_id]
    except KeyError as error:
        available = ", ".join(sorted(SCENARIOS))
        raise KeyError(f"Unknown scenario {scenario_id!r}. Available: {available}") from error
