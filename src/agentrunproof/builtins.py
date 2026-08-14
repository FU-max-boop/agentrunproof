from __future__ import annotations

from agents import Agent, function_tool

from .model import DeterministicModel, assistant_message, function_call
from .scenario import (
    Decision,
    ExpectedOutcome,
    LiteralInput,
    OutcomeKind,
    ResumeInput,
    RunVariant,
    Scenario,
    ScenarioCase,
    ScenarioPhase,
    ScenarioPlan,
)
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


def _runstate_sibling_approval_isolation_case(variant: RunVariant) -> ScenarioPlan:
    del variant
    call_id = "agentrunproof-sibling-call-1"
    tool_counts = {"approval_tool": 0}

    @function_tool(needs_approval=True)
    def approval_tool() -> str:
        """Record one deterministic synthetic approved invocation."""

        tool_counts["approval_tool"] += 1
        return "approved"

    model = DeterministicModel(
        [
            [function_call("approval_tool", {}, call_id=call_id)],
            [assistant_message("unexpected sibling approval leak")],
        ]
    )
    agent = Agent(
        name="AgentRunProof RunState sibling isolation agent",
        instructions="Request the synthetic approval tool, then stop for approval.",
        model=model,
        tools=[approval_tool],
    )
    interrupted = ExpectedOutcome(
        kind=OutcomeKind.INTERRUPTED,
        interruption_count=1,
    )
    return ScenarioPlan(
        phases=(
            ScenarioPhase(
                phase_id="initial",
                agent=agent,
                input=LiteralInput("Request the public synthetic approval tool."),
                model=model,
                tool_counts=tool_counts,
                expected_outcome=interrupted,
                expected_tool_counts_delta={"approval_tool": 0},
                model_group="approval-model",
            ),
            ScenarioPhase(
                phase_id="fork-check",
                agent=agent,
                input=ResumeInput(
                    source_phase="initial",
                    json_round_trip=False,
                    sibling_decisions=(Decision(call_id),),
                ),
                model=model,
                tool_counts=tool_counts,
                expected_outcome=interrupted,
                expected_tool_counts_delta={"approval_tool": 0},
                model_group="approval-model",
            ),
        )
    )


RUNSTATE_SIBLING_APPROVAL_ISOLATION = Scenario(
    scenario_id="runstate-sibling-approval-isolation",
    revision=1,
    description=(
        "Approving one direct RunState sibling must not mutate another sibling from the same result."
    ),
    variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
    invariants=(
        "execution_outcome",
        "state_transitions",
        "state_fork_isolation",
        "phase_contract",
        "tool_linkage",
        "exactly_once",
        "stream_parity",
    ),
    factory=_runstate_sibling_approval_isolation_case,
    expected_tool_counts={"approval_tool": 0},
    public_payloads=True,
)


SCENARIOS: dict[str, Scenario] = {
    BASIC_TOOL_SESSION_PARITY.scenario_id: BASIC_TOOL_SESSION_PARITY,
    RUNSTATE_SIBLING_APPROVAL_ISOLATION.scenario_id: RUNSTATE_SIBLING_APPROVAL_ISOLATION,
}


def get_scenario(scenario_id: str) -> Scenario:
    try:
        return SCENARIOS[scenario_id]
    except KeyError as error:
        available = ", ".join(sorted(SCENARIOS))
        raise KeyError(f"Unknown scenario {scenario_id!r}. Available: {available}") from error
