from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, cast

from agents import (
    Agent,
    GuardrailFunctionOutput,
    InputGuardrail,
    OutputGuardrail,
    RunContextWrapper,
    function_tool,
    handoff,
)
from agents.extensions.handoff_filters import nest_handoff_history, remove_all_tools
from agents.handoffs import (
    HandoffInputData,
    get_conversation_history_wrappers,
    set_conversation_history_wrappers,
)
from agents.items import TResponseInputItem

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


_HANDOFF_CONTEXT_MARKERS = (
    "prior-user-alpha",
    "prior-answer-alpha",
    "current-user-alpha",
    "route-domain-alpha",
    "<CONVERSATION HISTORY>",
)
_HANDOFF_EXCLUDED_CONTEXT_MARKERS = (
    "prior_lookup",
    "prior-alpha",
    "prior-tool-alpha",
    "lookup_case",
    "lookup-alpha",
    "risk-low-alpha",
    "transfer_to_domain_specialist",
    "handoff-domain",
)
_DEFAULT_HISTORY_WRAPPERS = ("<CONVERSATION HISTORY>", "</CONVERSATION HISTORY>")


def _function_item_types(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    item_types: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type in {
            "function_call",
            "function_call_output",
        }:
            item_types.append(item_type)
    return item_types


def _marker_occurrences(value: Any, markers: Sequence[str]) -> dict[str, int]:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {marker: encoded.count(marker) for marker in markers}


def _guardrail_payload(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _handoff_session_filtered_view_case(variant: RunVariant) -> ScenarioPlan:
    del variant
    tool_counts = {"lookup_case": 0}
    guardrail_events: list[dict[str, str]] = []
    wrapper_restore_checks: list[bool] = []

    @function_tool(name_override="lookup_case")
    def lookup_case(case_id: str) -> str:
        """Return one deterministic risk label for a public synthetic case."""

        tool_counts["lookup_case"] += 1
        return "risk-low-alpha" if case_id == "alpha" else "risk-unknown"

    def input_guardrail(
        _context: RunContextWrapper[Any],
        agent: Agent[Any],
        _input: str | list[TResponseInputItem],
    ) -> GuardrailFunctionOutput:
        event = {
            "stage": "input",
            "agent": agent.name,
            "payload": _guardrail_payload(_input),
        }
        guardrail_events.append(event)
        return GuardrailFunctionOutput(
            output_info=event,
            tripwire_triggered=False,
        )

    def output_guardrail(
        _context: RunContextWrapper[Any],
        agent: Agent[Any],
        _output: Any,
    ) -> GuardrailFunctionOutput:
        event = {
            "stage": "output",
            "agent": agent.name,
            "payload": _guardrail_payload(_output),
        }
        guardrail_events.append(event)
        return GuardrailFunctionOutput(
            output_info=event,
            tripwire_triggered=False,
        )

    input_guardrail_contract = InputGuardrail(guardrail_function=input_guardrail)
    output_guardrail_contract = OutputGuardrail(guardrail_function=output_guardrail)

    model = DeterministicModel(
        [
            [
                function_call(
                    "lookup_case",
                    {"case_id": "alpha"},
                    call_id="lookup-alpha",
                    item_id="lookup-alpha-item",
                )
            ],
            [
                assistant_message("route-domain-alpha", item_id="route-alpha-item"),
                function_call(
                    "transfer_to_domain_specialist",
                    {},
                    call_id="handoff-domain",
                    item_id="handoff-domain-item",
                ),
            ],
            [assistant_message("resolved-alpha", item_id="resolved-alpha-item")],
        ]
    )
    specialist = Agent(
        name="Domain Specialist",
        instructions="Resolve the routed public synthetic case.",
        model=model,
        input_guardrails=[input_guardrail_contract],
        output_guardrails=[output_guardrail_contract],
    )

    def structured_tool_free_history(data: HandoffInputData) -> HandoffInputData:
        # Tool records must be removed before nesting serializes transcript items into text.
        previous_wrappers = get_conversation_history_wrappers()
        set_conversation_history_wrappers(
            start=_DEFAULT_HISTORY_WRAPPERS[0],
            end=_DEFAULT_HISTORY_WRAPPERS[1],
        )
        try:
            return nest_handoff_history(remove_all_tools(data))
        finally:
            set_conversation_history_wrappers(
                start=previous_wrappers[0],
                end=previous_wrappers[1],
            )
            wrapper_restore_checks.append(get_conversation_history_wrappers() == previous_wrappers)

    route_to_specialist = handoff(
        specialist,
        tool_name_override="transfer_to_domain_specialist",
        input_filter=structured_tool_free_history,
    )
    triage = Agent(
        name="Triage Agent",
        instructions="Look up the public synthetic case, then route it to the specialist.",
        model=model,
        tools=[lookup_case],
        handoffs=[route_to_specialist],
        input_guardrails=[input_guardrail_contract],
        output_guardrails=[output_guardrail_contract],
    )
    history = cast(
        list[TResponseInputItem],
        [
            {"role": "user", "content": "prior-user-alpha"},
            {
                "type": "function_call",
                "name": "prior_lookup",
                "arguments": "{}",
                "call_id": "prior-alpha",
                "id": "prior-alpha-item",
            },
            {
                "type": "function_call_output",
                "call_id": "prior-alpha",
                "output": "prior-tool-alpha",
            },
            {"role": "assistant", "content": "prior-answer-alpha"},
        ],
    )
    session = RecordingSession(items=history)

    def capture_guardrail_configuration() -> dict[str, int]:
        return {
            "source_input": len(triage.input_guardrails),
            "source_output": len(triage.output_guardrails),
            "target_input": len(specialist.input_guardrails),
            "target_output": len(specialist.output_guardrails),
        }

    def capture_guardrail_events() -> list[dict[str, str]]:
        return [dict(event) for event in guardrail_events]

    def capture_model_tool_visibility() -> list[list[str]]:
        return [_function_item_types(call.input) for call in model.calls]

    def capture_session_tool_history() -> list[str]:
        return _function_item_types(session.snapshot())

    def capture_specialist_context() -> dict[str, int]:
        value = model.calls[-1].input if model.calls else []
        return _marker_occurrences(
            value,
            _HANDOFF_CONTEXT_MARKERS + _HANDOFF_EXCLUDED_CONTEXT_MARKERS,
        )

    def capture_model_handoff_counts() -> list[int]:
        return [len(call.handoffs) for call in model.calls]

    def capture_wrapper_restoration() -> list[bool]:
        return list(wrapper_restore_checks)

    return ScenarioPlan(
        phases=(
            ScenarioPhase(
                phase_id="handoff",
                agent=triage,
                input=LiteralInput("current-user-alpha"),
                model=model,
                session=session,
                max_turns=5,
                tool_counts=tool_counts,
                expected_tool_counts_delta={"lookup_case": 1},
                probes=(
                    StateProbe(
                        "guardrail_configuration",
                        capture_guardrail_configuration,
                        {
                            "source_input": 1,
                            "source_output": 1,
                            "target_input": 1,
                            "target_output": 1,
                        },
                    ),
                    StateProbe(
                        "guardrail_events",
                        capture_guardrail_events,
                        [
                            {
                                "stage": "input",
                                "agent": "Triage Agent",
                                "payload": _guardrail_payload(
                                    [*history, {"role": "user", "content": "current-user-alpha"}]
                                ),
                            },
                            {
                                "stage": "output",
                                "agent": "Domain Specialist",
                                "payload": "resolved-alpha",
                            },
                        ],
                    ),
                    StateProbe(
                        "model_handoff_counts",
                        capture_model_handoff_counts,
                        [1, 1, 0],
                    ),
                    StateProbe(
                        "model_tool_visibility",
                        capture_model_tool_visibility,
                        [
                            ["function_call", "function_call_output"],
                            [
                                "function_call",
                                "function_call_output",
                                "function_call",
                                "function_call_output",
                            ],
                            [],
                        ],
                    ),
                    StateProbe(
                        "session_tool_history",
                        capture_session_tool_history,
                        [
                            "function_call",
                            "function_call_output",
                            "function_call",
                            "function_call_output",
                        ],
                    ),
                    StateProbe(
                        "specialist_context",
                        capture_specialist_context,
                        {
                            **{marker: 1 for marker in _HANDOFF_CONTEXT_MARKERS},
                            **{marker: 0 for marker in _HANDOFF_EXCLUDED_CONTEXT_MARKERS},
                        },
                    ),
                    StateProbe(
                        "wrapper_restoration",
                        capture_wrapper_restoration,
                        [True],
                    ),
                ),
                model_group="handoff-model",
            ),
        )
    )


HANDOFF_SESSION_FILTERED_VIEW_PARITY = Scenario(
    scenario_id="handoff-session-filtered-view-parity",
    revision=1,
    description=(
        "A filtered handoff hides tool items from the specialist while the session retains full "
        "tool causality across runner modes."
    ),
    variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
    invariants=(
        "execution_outcome",
        "phase_contract",
        "stream_parity",
        "tool_linkage",
        "exactly_once",
        "model_script_consumed",
        "session_replay",
    ),
    factory=_handoff_session_filtered_view_case,
    expected_tool_counts={"lookup_case": 1},
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
    HANDOFF_SESSION_FILTERED_VIEW_PARITY.scenario_id: HANDOFF_SESSION_FILTERED_VIEW_PARITY,
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
