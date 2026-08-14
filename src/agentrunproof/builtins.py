from __future__ import annotations

from dataclasses import replace

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
    StateProbe,
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


def _runstate_recursive_agent_tool_approval_routing_case(
    variant: RunVariant,
) -> ScenarioPlan:
    del variant
    protected_call_id = "agentrunproof-recursive-protected-call-1"
    tool_counts = {"protected_effect": 0}
    effects: list[str] = []

    @function_tool(name_override="protected_effect", needs_approval=True)
    async def protected_effect(value: str) -> str:
        """Commit one deterministic synthetic effect after explicit approval."""

        tool_counts["protected_effect"] += 1
        effects.append(value)
        return "approved"

    inner_model = DeterministicModel(
        [
            [
                function_call(
                    protected_effect.name,
                    {"value": "committed-once"},
                    call_id=protected_call_id,
                    item_id="agentrunproof-recursive-protected-item-1",
                )
            ],
            [assistant_message("inner complete", item_id="agentrunproof-recursive-inner-done")],
        ]
    )
    inner_agent = Agent(
        name="AgentRunProof recursive approval inner agent",
        instructions="Request the protected effect and finish after it is approved.",
        model=inner_model,
        tools=[protected_effect],
    )
    inner_tool = inner_agent.as_tool(
        tool_name="run_recursive_inner_agent",
        tool_description="Run the inner approval-gated synthetic agent.",
    )
    middle_model = DeterministicModel(
        [
            [
                function_call(
                    inner_tool.name,
                    {"input": "run the inner agent"},
                    call_id="agentrunproof-recursive-inner-edge-call-1",
                    item_id="agentrunproof-recursive-inner-edge-item-1",
                )
            ],
            [assistant_message("middle complete", item_id="agentrunproof-recursive-middle-done")],
        ]
    )
    middle_agent = Agent(
        name="AgentRunProof recursive approval middle agent",
        instructions="Run the inner agent tool and return its completion.",
        model=middle_model,
        tools=[inner_tool],
    )
    middle_tool = middle_agent.as_tool(
        tool_name="run_recursive_middle_agent",
        tool_description="Run the middle synthetic agent.",
    )
    outer_model = DeterministicModel(
        [
            [
                function_call(
                    middle_tool.name,
                    {"input": "run the middle agent"},
                    call_id="agentrunproof-recursive-middle-edge-call-1",
                    item_id="agentrunproof-recursive-middle-edge-item-1",
                )
            ],
            [assistant_message("outer complete", item_id="agentrunproof-recursive-outer-done")],
        ]
    )
    outer_agent = Agent(
        name="AgentRunProof recursive approval outer agent",
        instructions="Run the middle agent tool and return its completion.",
        model=outer_model,
        tools=[middle_tool],
    )

    def capture_effects() -> list[str]:
        return list(effects)

    def capture_inner_remaining() -> int:
        return inner_model.remaining_steps

    def capture_middle_remaining() -> int:
        return middle_model.remaining_steps

    def capture_outer_remaining() -> int:
        return outer_model.remaining_steps

    def probes(*, expected_effects: list[str], remaining: int) -> tuple[StateProbe, ...]:
        return (
            StateProbe("protected_effects", capture_effects, expected_effects),
            StateProbe("inner_model_remaining", capture_inner_remaining, remaining),
            StateProbe("middle_model_remaining", capture_middle_remaining, remaining),
            StateProbe("outer_model_remaining", capture_outer_remaining, remaining),
        )

    interrupted = ExpectedOutcome(kind=OutcomeKind.INTERRUPTED, interruption_count=1)
    return ScenarioPlan(
        phases=(
            ScenarioPhase(
                phase_id="initial",
                agent=outer_agent,
                input=LiteralInput("Run the two-edge nested agent chain."),
                model=outer_model,
                tool_counts=tool_counts,
                expected_outcome=interrupted,
                expected_tool_counts_delta={"protected_effect": 0},
                probes=probes(expected_effects=[], remaining=1),
                model_group="recursive-outer-model",
            ),
            ScenarioPhase(
                phase_id="untouched-sibling",
                agent=outer_agent,
                input=ResumeInput(
                    source_phase="initial",
                    json_round_trip=False,
                    sibling_decisions=(Decision(protected_call_id),),
                    save_sibling_state=True,
                ),
                model=outer_model,
                tool_counts=tool_counts,
                expected_outcome=interrupted,
                expected_tool_counts_delta={"protected_effect": 0},
                probes=probes(expected_effects=[], remaining=1),
                model_group="recursive-outer-model",
            ),
            ScenarioPhase(
                phase_id="approved-sibling",
                agent=outer_agent,
                input=ResumeInput(
                    source_phase="initial",
                    json_round_trip=False,
                    saved_sibling_from="untouched-sibling",
                ),
                model=outer_model,
                tool_counts=tool_counts,
                expected_tool_counts_delta={"protected_effect": 1},
                probes=probes(expected_effects=["committed-once"], remaining=0),
                model_group="recursive-outer-model",
            ),
        )
    )


RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_ROUTING = Scenario(
    scenario_id="runstate-recursive-agent-tool-approval-routing",
    revision=1,
    description=(
        "One flattened approval must route through two Agent.as_tool checkpoints while a direct "
        "sibling state remains pending."
    ),
    variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
    invariants=(
        "execution_outcome",
        "state_transitions",
        "state_fork_isolation",
        "recursive_approval_routing",
        "phase_contract",
        "exactly_once",
        "model_script_consumed",
        "stream_parity",
    ),
    factory=_runstate_recursive_agent_tool_approval_routing_case,
    expected_tool_counts={"protected_effect": 1},
    public_payloads=True,
)


def _runstate_recursive_agent_tool_approval_serialization_case(
    variant: RunVariant,
) -> ScenarioPlan:
    base = _runstate_recursive_agent_tool_approval_routing_case(variant)
    initial = base.phases[0]
    approved_template = base.phases[2]
    approved = replace(
        approved_template,
        phase_id="serialized-approved",
        input=ResumeInput(
            source_phase="initial",
            decisions=(Decision("agentrunproof-recursive-protected-call-1"),),
            json_round_trip=True,
        ),
    )
    return ScenarioPlan(phases=(initial, approved))


RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_SERIALIZATION = Scenario(
    scenario_id="runstate-recursive-agent-tool-approval-serialization",
    revision=1,
    description=(
        "One flattened approval must survive RunState JSON restoration and route through two "
        "Agent.as_tool checkpoints."
    ),
    variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
    invariants=(
        "execution_outcome",
        "state_transitions",
        "phase_contract",
        "exactly_once",
        "model_script_consumed",
        "stream_parity",
    ),
    factory=_runstate_recursive_agent_tool_approval_serialization_case,
    expected_tool_counts={"protected_effect": 1},
    public_payloads=True,
)


SCENARIOS: dict[str, Scenario] = {
    BASIC_TOOL_SESSION_PARITY.scenario_id: BASIC_TOOL_SESSION_PARITY,
    RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_SERIALIZATION.scenario_id: (
        RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_SERIALIZATION
    ),
    RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_ROUTING.scenario_id: (
        RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_ROUTING
    ),
    RUNSTATE_SIBLING_APPROVAL_ISOLATION.scenario_id: RUNSTATE_SIBLING_APPROVAL_ISOLATION,
}


def get_scenario(scenario_id: str) -> Scenario:
    try:
        return SCENARIOS[scenario_id]
    except KeyError as error:
        available = ", ".join(sorted(SCENARIOS))
        raise KeyError(f"Unknown scenario {scenario_id!r}. Available: {available}") from error
