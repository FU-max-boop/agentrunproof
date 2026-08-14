from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from agents import (
    Agent,
    GuardrailFunctionOutput,
    OutputGuardrail,
    RunContextWrapper,
    function_tool,
)

from .._canonical import JsonValue, to_json_value
from ..engine import ProofRun, run_scenario
from ..model import DeterministicModel, assistant_message, function_call
from ..scenario import (
    Decision,
    ExpectedOutcome,
    LiteralInput,
    OutcomeKind,
    ResumeInput,
    RunVariant,
    Scenario,
    ScenarioPhase,
    ScenarioPlan,
    StateProbe,
)
from ..session import RecordingSession

_CALL_ID = "call-approved"


async def resumed_guardrail_atomicity() -> dict[str, JsonValue]:
    """Exercise the public-API reproducer for openai-agents-python issue #4125."""

    proof = await resumed_guardrail_atomicity_proof()
    variants = {
        variant.value: to_json_value(_history_observation(observation))
        for variant, observation in proof.observations.items()
    }
    return {
        "case_id": "openai-agents-python-4125-resumed-guardrail-atomicity",
        "case_revision": 1,
        "overall_status": proof.status,
        "public_api_only": True,
        "variants": variants,
    }


async def resumed_guardrail_atomicity_proof() -> ProofRun:
    def build(variant: RunVariant) -> ScenarioPlan:
        del variant
        side_effects: list[str] = []
        tool_counts = {"approval_tool": 0}
        guardrail_calls = {"count": 0}

        @function_tool(name_override="approval_tool", needs_approval=True)
        def approval_tool() -> str:
            side_effects.append("ran")
            tool_counts["approval_tool"] += 1
            return "approved-result"

        def output_guardrail(
            _context: RunContextWrapper[Any],
            _agent: Agent[Any],
            _output: Any,
        ) -> GuardrailFunctionOutput:
            guardrail_calls["count"] += 1
            return GuardrailFunctionOutput(
                output_info=None,
                tripwire_triggered=guardrail_calls["count"] == 1,
            )

        def capture_side_effects() -> list[str]:
            return list(side_effects)

        model = DeterministicModel(
            [
                [function_call("approval_tool", {}, call_id=_CALL_ID)],
                [assistant_message("done", item_id="agentrunproof-followup-message")],
            ]
        )
        agent = Agent(
            name="AgentRunProof history #4125",
            model=model,
            tools=[approval_tool],
            tool_use_behavior="stop_on_first_tool",
            output_guardrails=[OutputGuardrail(guardrail_function=output_guardrail)],
        )
        session = RecordingSession()
        return ScenarioPlan(
            phases=(
                ScenarioPhase(
                    phase_id="initial",
                    agent=agent,
                    input=LiteralInput("Use approval_tool"),
                    model=model,
                    session=session,
                    tool_counts=tool_counts,
                    expected_outcome=ExpectedOutcome(
                        kind=OutcomeKind.INTERRUPTED,
                        interruption_count=1,
                    ),
                    expected_tool_counts_delta={"approval_tool": 0},
                    probes=(StateProbe("side_effects", capture_side_effects, []),),
                    model_group="guardrail-model",
                ),
                ScenarioPhase(
                    phase_id="resume",
                    agent=agent,
                    input=ResumeInput(
                        source_phase="initial",
                        decisions=(Decision(_CALL_ID),),
                        json_round_trip=False,
                    ),
                    model=model,
                    session=session,
                    tool_counts=tool_counts,
                    expected_outcome=ExpectedOutcome(
                        kind=OutcomeKind.RAISES,
                        exception_type="agents.exceptions.OutputGuardrailTripwireTriggered",
                    ),
                    expected_tool_counts_delta={"approval_tool": 1},
                    probes=(StateProbe("side_effects", capture_side_effects, ["ran"]),),
                    model_group="guardrail-model",
                ),
                ScenarioPhase(
                    phase_id="followup",
                    agent=agent,
                    input=LiteralInput("Continue"),
                    model=model,
                    session=session,
                    tool_counts=tool_counts,
                    expected_tool_counts_delta={"approval_tool": 0},
                    probes=(StateProbe("side_effects", capture_side_effects, ["ran"]),),
                    model_group="guardrail-model",
                ),
            )
        )

    scenario = Scenario(
        scenario_id="openai-agents-python-4125-resumed-guardrail-atomicity",
        revision=1,
        description="Approved tool output remains durable when a resumed output guardrail trips.",
        variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
        invariants=(
            "execution_outcome",
            "state_transitions",
            "phase_contract",
            "tool_linkage",
            "session_replay",
            "model_script_consumed",
            "stream_parity",
        ),
        factory=build,
        public_payloads=True,
    )
    return await run_scenario(scenario)


def _history_observation(observation: Any) -> dict[str, Any]:
    initial, resumed, followup = observation.phases
    session_after_tripwire = resumed.session_items_after
    session_after_followup = followup.session_items_after
    followup_model_input = _first_model_input(followup)
    durable_tool_sequence = _tool_sequence(session_after_tripwire)
    replayed_tool_sequence = _tool_sequence(followup_model_input)
    expected_tool_sequence = [
        {"type": "function_call", "call_id": _CALL_ID},
        {
            "type": "function_call_output",
            "call_id": _CALL_ID,
            "output": "approved-result",
        },
    ]
    first_exception = _short_exception_type(initial.exception)
    resume_exception = _short_exception_type(resumed.exception)
    followup_exception = _short_exception_type(followup.exception)
    side_effects = resumed.probes_after.get("side_effects")
    side_effect_count = len(side_effects) if isinstance(side_effects, list) else 0

    violations: list[str] = []
    if first_exception is not None or initial.interruption_count != 1:
        violations.append("EXPECTED_APPROVAL_INTERRUPTION_MISSING")
    if resume_exception != "OutputGuardrailTripwireTriggered":
        violations.append("RESUME_DID_NOT_TRIP_OUTPUT_GUARDRAIL")
    if side_effect_count != 1:
        violations.append("SIDE_EFFECT_COUNT_NOT_ONE")
    if durable_tool_sequence != expected_tool_sequence:
        violations.append("COMMITTED_TOOL_PAIR_NOT_DURABLE")
    if replayed_tool_sequence != expected_tool_sequence:
        violations.append("FOLLOWUP_MODEL_INPUT_MISSING_TOOL_PAIR")
    if followup_exception is not None or followup.final_output != "done":
        violations.append("FOLLOWUP_DID_NOT_COMPLETE")

    return {
        "status": "PASS" if not violations else "FAIL",
        "reason": "OK" if not violations else violations[0],
        "violations": violations,
        "interruption_count": initial.interruption_count,
        "resume_exception_type": resume_exception,
        "side_effect_count": side_effect_count,
        "session_after_tripwire": session_after_tripwire,
        "session_after_followup": session_after_followup,
        "durable_tool_sequence": durable_tool_sequence,
        "followup_model_input": followup_model_input,
        "replayed_tool_sequence": replayed_tool_sequence,
        "followup_final_output": followup.final_output,
        "first_exception_type": first_exception,
        "followup_exception_type": followup_exception,
    }


def _first_model_input(phase: Any) -> JsonValue:
    if not phase.model_calls:
        return None
    call = phase.model_calls[0]
    return call.get("input") if isinstance(call, dict) else None


def _short_exception_type(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    exception_type = value.get("type")
    return exception_type.rsplit(".", 1)[-1] if isinstance(exception_type, str) else None


def _tool_sequence(items: Any) -> list[dict[str, JsonValue]]:
    if not isinstance(items, Sequence) or isinstance(items, str | bytes | bytearray):
        return []
    result: list[dict[str, JsonValue]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if item_type not in {"function_call", "function_call_output"}:
            continue
        normalized: dict[str, JsonValue] = {"type": cast(str, item_type)}
        call_id = item.get("call_id")
        if isinstance(call_id, str):
            normalized["call_id"] = call_id
        if item_type == "function_call_output":
            normalized["output"] = to_json_value(item.get("output"))
        result.append(normalized)
    return result
