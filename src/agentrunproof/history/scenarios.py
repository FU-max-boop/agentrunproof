from __future__ import annotations

import json
from typing import Any, cast

from agents import Agent, FunctionTool, RunConfig, Runner
from agents.items import TResponseInputItem
from agents.memory.session_settings import SessionSettings

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
from .guardrail_atomicity import resumed_guardrail_atomicity


async def session_limit_orphan_output() -> dict[str, JsonValue]:
    """Exercise the public-API reproducer for openai-agents-python issue #4322."""

    observations: dict[str, JsonValue] = {}
    for variant in (RunVariant.NON_STREAMING, RunVariant.STREAMING):
        observations[variant.value] = to_json_value(await _session_limit_variant(variant))
    statuses = {
        observation["status"]
        for observation in observations.values()
        if isinstance(observation, dict)
    }
    return {
        "case_id": "openai-agents-python-4322-session-limit-orphan-output",
        "case_revision": 1,
        "overall_status": "PASS" if statuses == {"PASS"} else "FAIL",
        "public_api_only": True,
        "variants": observations,
    }


async def runstate_context_approval() -> dict[str, JsonValue]:
    """Exercise the public-API reproducer for openai-agents-python issue #4244."""

    proof = await runstate_context_approval_proof()
    observations = {
        variant.value: to_json_value(_approval_history_observation(observation))
        for variant, observation in proof.observations.items()
    }
    return {
        "case_id": "openai-agents-python-4244-runstate-context-approval",
        "case_revision": 1,
        "overall_status": proof.status,
        "public_api_only": True,
        "variants": observations,
    }


async def runstate_context_approval_proof(*, public_payloads: bool = True) -> ProofRun:
    expected_call_id = "approval-call-1"
    original_context = {"principal": "original"}
    override_context = {"principal": "ella"}

    def build(variant: RunVariant) -> ScenarioPlan:
        del variant
        invocations: list[dict[str, Any]] = []
        tool_counts = {"approval_tool": 0}

        async def invoke_tool(context: Any, arguments_json: str) -> str:
            arguments = json.loads(arguments_json)
            application_context = getattr(context, "context", None)
            principal = (
                application_context.get("principal")
                if isinstance(application_context, dict)
                else None
            )
            call_id = getattr(context, "tool_call_id", None)
            invocations.append(
                {
                    "arguments": arguments,
                    "call_id": call_id if isinstance(call_id, str) else None,
                    "principal": principal if isinstance(principal, str) else None,
                }
            )
            tool_counts["approval_tool"] += 1
            return "tool-ran"

        def approval_tool() -> FunctionTool:
            return FunctionTool(
                name="approval_tool",
                description="A deterministic synthetic tool that requires approval.",
                params_json_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                on_invoke_tool=invoke_tool,
                strict_json_schema=False,
                needs_approval=True,
            )

        initial_model = DeterministicModel(
            [[function_call("approval_tool", {}, call_id=expected_call_id)]]
        )
        resume_model = DeterministicModel([[assistant_message("done")]])
        initial_agent = Agent(
            name="AgentRunProof history #4244",
            model=initial_model,
            tools=[approval_tool()],
        )
        resume_agent = Agent(
            name="AgentRunProof history #4244",
            model=resume_model,
            tools=[approval_tool()],
        )

        def invocation_probe() -> list[dict[str, Any]]:
            return list(invocations)

        return ScenarioPlan(
            phases=(
                ScenarioPhase(
                    phase_id="initial",
                    agent=initial_agent,
                    input=LiteralInput("Invoke the approval tool."),
                    model=initial_model,
                    context=original_context,
                    tool_counts=tool_counts,
                    expected_outcome=ExpectedOutcome(
                        kind=OutcomeKind.INTERRUPTED,
                        interruption_count=1,
                    ),
                    expected_tool_counts_delta={"approval_tool": 0},
                    probes=(StateProbe("tool_invocations", invocation_probe, []),),
                    model_group="initial-model",
                ),
                ScenarioPhase(
                    phase_id="resume",
                    agent=resume_agent,
                    input=ResumeInput(
                        source_phase="initial",
                        decisions=(Decision(expected_call_id),),
                        json_round_trip=True,
                    ),
                    model=resume_model,
                    context=override_context,
                    tool_counts=tool_counts,
                    expected_outcome=ExpectedOutcome(),
                    expected_tool_counts_delta={"approval_tool": 1},
                    probes=(
                        StateProbe(
                            "tool_invocations",
                            invocation_probe,
                            [
                                {
                                    "arguments": {},
                                    "call_id": expected_call_id,
                                    "principal": "ella",
                                }
                            ],
                        ),
                    ),
                    model_group="resume-model",
                ),
            )
        )

    scenario = Scenario(
        scenario_id="openai-agents-python-4244-runstate-context-approval",
        revision=1,
        description="RunState JSON approval survives an exact context-overridden resume.",
        variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
        invariants=(
            "execution_outcome",
            "state_transitions",
            "phase_contract",
            "tool_linkage",
            "model_script_consumed",
            "stream_parity",
        ),
        factory=build,
        public_payloads=public_payloads,
    )
    proof = await run_scenario(scenario)
    return proof


def _approval_history_observation(observation: Any) -> dict[str, Any]:
    initial, resumed = observation.phases
    transition = resumed.state_transition
    initial_ids = _known_call_ids(initial.interruption_call_ids, "approval-call-1")
    restored_ids = _known_call_ids(transition["restored_interruption_call_ids"], "approval-call-1")
    resumed_ids = _known_call_ids(resumed.interruption_call_ids, "approval-call-1")
    invocations = resumed.probes_after.get("tool_invocations")
    if not isinstance(invocations, list):
        invocations = []
    error = initial.exception or resumed.exception
    exception_type = _short_exception_type(error)
    reason = _context_approval_reason(
        error=exception_type,
        expected_call_id="approval-call-1",
        initial_interruption_ids=initial_ids,
        restored_interruption_ids=restored_ids,
        resumed_interruption_ids=resumed_ids,
        serialized_round_trip=transition.get("json_round_trip_equal") is True,
        invocations=cast(list[dict[str, Any]], invocations),
        final_output=resumed.final_output,
        resumed_model_remaining_steps=resumed.remaining_model_steps,
    )
    return {
        "status": "PASS" if reason == "OK" else "FAIL",
        "reason": reason,
        "initial_interruption_call_ids": initial_ids,
        "restored_interruption_call_ids": restored_ids,
        "resumed_interruption_call_ids": resumed_ids,
        "approved_call_id": restored_ids[0] if len(restored_ids) == 1 else None,
        "tool_invocations": invocations,
        "tool_invocation_count": len(invocations),
        "final_output": resumed.final_output,
        "state_json_round_trip": transition.get("json_round_trip_equal") is True,
        "state_schema_version": transition.get("state_schema_version"),
        "initial_model_call_count": len(initial.model_calls),
        "resumed_model_call_count": len(resumed.model_calls),
        "resumed_model_remaining_steps": resumed.remaining_model_steps,
        "exception_type": exception_type,
    }


def _known_call_ids(values: Any, expected: str) -> list[str | None]:
    from .._canonical import sha256_hex

    if not isinstance(values, list):
        return []
    expected_digest = sha256_hex(expected)
    return [expected if value == expected_digest else None for value in values]


def _short_exception_type(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    exception_type = value.get("type")
    return exception_type.rsplit(".", 1)[-1] if isinstance(exception_type, str) else None


def _context_approval_reason(
    *,
    error: str | None,
    expected_call_id: str,
    initial_interruption_ids: list[str | None],
    restored_interruption_ids: list[str | None],
    resumed_interruption_ids: list[str | None],
    serialized_round_trip: bool,
    invocations: list[dict[str, Any]],
    final_output: Any,
    resumed_model_remaining_steps: int,
) -> str:
    if error is not None:
        return "SCENARIO_EXCEPTION"
    if initial_interruption_ids != [expected_call_id]:
        return "INITIAL_APPROVAL_INTERRUPTION_MISSING"
    if not serialized_round_trip:
        return "RUNSTATE_JSON_ROUND_TRIP_CHANGED"
    if restored_interruption_ids != [expected_call_id]:
        return "RESTORED_APPROVAL_INTERRUPTION_MISSING"
    if resumed_interruption_ids:
        return "APPROVAL_NOT_HONORED_WITH_CONTEXT_OVERRIDE"
    if len(invocations) != 1:
        return "APPROVED_TOOL_NOT_INVOKED_EXACTLY_ONCE"
    invocation = invocations[0]
    if invocation.get("call_id") != expected_call_id:
        return "APPROVED_TOOL_CALL_ID_CHANGED"
    if invocation.get("principal") != "ella":
        return "CONTEXT_OVERRIDE_NOT_VISIBLE_TO_TOOL"
    if invocation.get("arguments") != {}:
        return "APPROVED_TOOL_ARGUMENTS_CHANGED"
    if final_output != "done":
        return "RESUMED_FINAL_OUTPUT_MISMATCH"
    if resumed_model_remaining_steps != 0:
        return "RESUMED_MODEL_STEP_NOT_CONSUMED"
    return "OK"


async def _session_limit_variant(variant: RunVariant) -> dict[str, Any]:
    history = cast(
        list[TResponseInputItem],
        [
            {"role": "user", "content": "What is the public synthetic weather?"},
            {
                "type": "function_call",
                "call_id": "history-call-1",
                "name": "get_weather",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "history-call-1",
                "output": "sunny",
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "It is sunny.",
                        "annotations": [],
                    }
                ],
            },
        ],
    )
    session = RecordingSession(items=history)
    model = DeterministicModel([[assistant_message("Tomorrow is sunny too.")]])
    agent = Agent(name="AgentRunProof history #4322", model=model)
    run_config = RunConfig(
        tracing_disabled=True,
        session_settings=SessionSettings(limit=2),
    )
    error: Exception | None = None
    try:
        if variant is RunVariant.NON_STREAMING:
            await Runner.run(
                agent,
                "What about tomorrow?",
                session=session,
                run_config=run_config,
            )
        else:
            streamed = Runner.run_streamed(
                agent,
                "What about tomorrow?",
                session=session,
                run_config=run_config,
            )
            async for _ in streamed.stream_events():
                pass
    except Exception as caught:
        error = caught

    model_input = model.calls[0].input if model.calls else None
    orphan_ids = _orphan_output_ids(model_input)
    observed_types = _item_types(model_input)
    passed = error is None and orphan_ids == [] and observed_types == ["message", "user"]
    return {
        "status": "PASS" if passed else "FAIL",
        "reason": "OK" if passed else "ORPHAN_FUNCTION_OUTPUT_REACHED_MODEL",
        "model_input": model_input,
        "observed_types": observed_types,
        "orphan_call_ids": orphan_ids,
        "exception_type": type(error).__name__ if error is not None else None,
    }


def _orphan_output_ids(model_input: Any) -> list[str]:
    if not isinstance(model_input, list):
        return ["INVALID_MODEL_INPUT"]
    seen_calls: set[str] = set()
    orphan_outputs: list[str] = []
    for item in model_input:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        call_id = item.get("call_id")
        if item_type == "function_call" and isinstance(call_id, str):
            seen_calls.add(call_id)
        elif item_type == "function_call_output" and (
            not isinstance(call_id, str) or call_id not in seen_calls
        ):
            orphan_outputs.append(call_id if isinstance(call_id, str) else "INVALID_CALL_ID")
    return orphan_outputs


def _item_types(model_input: Any) -> list[str]:
    if not isinstance(model_input, list):
        return []
    result: list[str] = []
    for item in model_input:
        if not isinstance(item, dict):
            result.append(type(item).__name__)
            continue
        item_type = item.get("type")
        if isinstance(item_type, str):
            result.append(item_type)
        else:
            role = item.get("role")
            result.append(role if isinstance(role, str) else "unknown")
    return result


SCENARIOS = {
    "resumed-guardrail-atomicity": resumed_guardrail_atomicity,
    "runstate-context-approval": runstate_context_approval,
    "session-limit-orphan-output": session_limit_orphan_output,
}
