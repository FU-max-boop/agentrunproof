from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

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
_RAW_TOOL_OUTPUT = "approved-result"
_ToolOutputPolicy = Literal["raw", "redacted"]


async def resumed_guardrail_atomicity() -> dict[str, JsonValue]:
    """Exercise the public-API reproducer for openai-agents-python issue #4125."""

    proof = await resumed_guardrail_atomicity_proof()
    observations = {
        variant.value: _history_observation(observation)
        for variant, observation in proof.observations.items()
    }
    variants = {name: to_json_value(observation) for name, observation in observations.items()}
    return {
        "case_id": "openai-agents-python-4125-resumed-guardrail-atomicity",
        "case_revision": proof.scenario.revision,
        "overall_status": (
            "PASS"
            if proof.status == "PASS"
            and observations
            and all(value["status"] == "PASS" for value in observations.values())
            else "FAIL"
        ),
        "public_api_only": True,
        "variants": variants,
    }


async def resumed_guardrail_atomicity_proof() -> ProofRun:
    output_policy = _rejected_tool_output_policy()

    def build(variant: RunVariant) -> ScenarioPlan:
        del variant
        side_effects: list[str] = []
        tool_counts = {"approval_tool": 0}
        guardrail_calls = {"count": 0}

        @function_tool(name_override="approval_tool", needs_approval=True)
        def approval_tool() -> str:
            side_effects.append("ran")
            tool_counts["approval_tool"] += 1
            return _RAW_TOOL_OUTPUT

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
        revision=2 if output_policy == "redacted" else 1,
        description=(
            "Approved tool call/output linkage remains durable when a resumed output guardrail "
            "trips."
        ),
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
    tool_output_policy = _rejected_tool_output_policy()
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
    if not _tool_pair_is_linked(durable_tool_sequence):
        violations.append("COMMITTED_TOOL_PAIR_NOT_DURABLE")
    elif tool_output_policy == "raw" and not _tool_pair_matches_policy(
        durable_tool_sequence,
        policy=tool_output_policy,
    ):
        violations.append("COMMITTED_TOOL_PAIR_NOT_DURABLE")
    elif tool_output_policy == "redacted" and (
        not _tool_pair_matches_policy(durable_tool_sequence, policy=tool_output_policy)
        or not _payload_matches_policy(
            [session_after_tripwire, session_after_followup],
            policy=tool_output_policy,
        )
    ):
        violations.append("REJECTED_TOOL_OUTPUT_NOT_REDACTED_FROM_SESSION")
    if not _tool_pair_is_linked(replayed_tool_sequence):
        violations.append("FOLLOWUP_MODEL_INPUT_MISSING_TOOL_PAIR")
    elif tool_output_policy == "raw" and not _tool_pair_matches_policy(
        replayed_tool_sequence,
        policy=tool_output_policy,
    ):
        violations.append("FOLLOWUP_MODEL_INPUT_MISSING_TOOL_PAIR")
    elif tool_output_policy == "redacted" and (
        not _tool_pair_matches_policy(replayed_tool_sequence, policy=tool_output_policy)
        or not _payload_matches_policy(
            followup_model_input,
            policy=tool_output_policy,
        )
    ):
        violations.append("REJECTED_TOOL_OUTPUT_NOT_REDACTED_FROM_MODEL_INPUT")
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


def _rejected_tool_output_policy() -> _ToolOutputPolicy:
    try:
        sdk_version = importlib.metadata.version("openai-agents")
    except importlib.metadata.PackageNotFoundError as error:
        raise RuntimeError(
            "Cannot classify output-guardrail behavior because openai-agents is not installed."
        ) from error
    return _tool_output_policy_for_sdk(sdk_version)


def _tool_output_policy_for_sdk(sdk_version: str) -> _ToolOutputPolicy:
    match = re.match(r"^(\d+)\.(\d+)", sdk_version)
    if match is None:
        raise RuntimeError(
            f"Cannot classify openai-agents {sdk_version!r}; install a released version in the "
            "supported openai-agents>=0.20,<0.23 window."
        )
    release_family = (int(match.group(1)), int(match.group(2)))
    if release_family < (0, 19) or release_family >= (0, 23):
        raise RuntimeError(
            f"openai-agents {sdk_version} is outside the supported >=0.20,<0.23 window and the "
            "0.19.x historical-evidence boundary."
        )
    return "redacted" if release_family >= (0, 22) else "raw"


def _tool_pair_matches_policy(
    sequence: Sequence[Mapping[str, JsonValue]],
    *,
    policy: _ToolOutputPolicy,
) -> bool:
    if not _tool_pair_is_linked(sequence):
        return False
    output = sequence[1]["output"]
    if policy == "raw":
        return output == _RAW_TOOL_OUTPUT
    return output is not None and not _contains_raw_tool_output(output)


def _tool_pair_is_linked(sequence: Sequence[Mapping[str, JsonValue]]) -> bool:
    if len(sequence) != 2:
        return False
    if sequence[0] != {"type": "function_call", "call_id": _CALL_ID}:
        return False
    output_item = sequence[1]
    if output_item.get("type") != "function_call_output":
        return False
    if output_item.get("call_id") != _CALL_ID or "output" not in output_item:
        return False
    return True


def _contains_raw_tool_output(value: JsonValue) -> bool:
    if isinstance(value, str):
        return _RAW_TOOL_OUTPUT in value
    if isinstance(value, Mapping):
        return any(
            _RAW_TOOL_OUTPUT in key or _contains_raw_tool_output(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence):
        return any(_contains_raw_tool_output(item) for item in value)
    return False


def _payload_matches_policy(value: Any, *, policy: _ToolOutputPolicy) -> bool:
    if policy == "raw":
        return True
    return not _contains_raw_tool_output(to_json_value(value))
