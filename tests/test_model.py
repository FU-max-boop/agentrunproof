from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from agents import Agent, Runner
from agents.agent_output import AgentOutputSchema
from agents.handoffs import Handoff
from agents.run import RunConfig
from agents.tool import FunctionTool
from openai.types.responses import ResponseCompletedEvent, ResponseOutputItemDoneEvent
from pydantic import BaseModel

from agentrunproof.engine import run_scenario
from agentrunproof.model import (
    DeterministicModel,
    ModelCall,
    ModelStep,
    UnconsumedModelSteps,
    UnexpectedModelCall,
    assistant_message,
)
from agentrunproof.scenario import RunVariant, Scenario, ScenarioCase


@pytest.mark.asyncio
async def test_deterministic_model_drives_real_runner_in_both_modes() -> None:
    non_streaming = DeterministicModel([[assistant_message("done")]])
    result = await Runner.run(
        Agent(name="test", model=non_streaming),
        "hello",
        run_config=RunConfig(tracing_disabled=True),
    )
    assert result.final_output == "done"
    assert non_streaming.calls[0].streamed is False
    non_streaming.assert_complete()

    streaming = DeterministicModel([[assistant_message("done")]])
    streamed = Runner.run_streamed(
        Agent(name="test", model=streaming),
        "hello",
        run_config=RunConfig(tracing_disabled=True),
    )
    async for _ in streamed.stream_events():
        pass
    assert streamed.final_output == "done"
    assert streaming.calls[0].streamed is True
    streaming.assert_complete()


@pytest.mark.asyncio
async def test_deterministic_model_uses_sdk_scripted_model_when_available() -> None:
    testing = pytest.importorskip(
        "agents.testing",
        reason="agents.testing is first available in the upcoming SDK 0.21 release",
    )
    model = DeterministicModel([[assistant_message("done")]])

    assert isinstance(model._sdk_model, testing.ScriptedModel)
    result = await Runner.run(
        Agent(name="test", model=model),
        "hello",
        run_config=RunConfig(tracing_disabled=True),
    )

    assert result.final_output == "done"
    assert model.calls[0].streamed is False
    model.assert_complete()


@pytest.mark.asyncio
async def test_stream_uses_minimal_normalized_terminal_events() -> None:
    model = DeterministicModel([[assistant_message("done")]])
    events = [
        event
        async for event in model.stream_response(
            None,
            "input",
            Agent(name="settings source").model_settings,
            [],
            None,
            [],
            model_tracing_disabled(),
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        )
    ]
    assert [type(event) for event in events] == [
        ResponseOutputItemDoneEvent,
        ResponseCompletedEvent,
    ]


def model_tracing_disabled():
    from agents.models.interface import ModelTracing

    return ModelTracing.DISABLED


def test_model_script_errors_are_structured() -> None:
    model = DeterministicModel([[assistant_message("unused")]])
    with pytest.raises(UnconsumedModelSteps, match="1 scripted"):
        model.assert_complete()

    empty = DeterministicModel([])
    with pytest.raises(UnexpectedModelCall):
        empty._next_step()


def test_model_step_rejects_output_and_error() -> None:
    with pytest.raises(ValueError, match="cannot combine"):
        ModelStep(output=(assistant_message("no"),), error=RuntimeError("boom"))


async def _unused_tool(_context: Any, _arguments: str) -> str:
    return "unused"


async def _record_one_call(
    *,
    tools: list[FunctionTool] | None = None,
    handoffs: list[Handoff[Any, Any]] | None = None,
    output_schema: AgentOutputSchema[Any] | None = None,
) -> ModelCall:
    model = DeterministicModel([[assistant_message("done")]])
    await model.get_response(
        None,
        "input",
        Agent(name="settings source").model_settings,
        tools or [],
        output_schema,
        handoffs or [],
        model_tracing_disabled(),
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    )
    return model.calls[0]


def _function_tool(
    *,
    description: str = "Look up a record.",
    schema: dict[str, Any] | None = None,
    needs_approval: (bool | Callable[[Any, dict[str, Any], str], Awaitable[bool]]) = False,
    strict_json_schema: bool = True,
    timeout_seconds: float | None = None,
    timeout_behavior: str = "error_as_result",
) -> FunctionTool:
    return FunctionTool(
        name="lookup",
        description=description,
        params_json_schema=(
            schema
            if schema is not None
            else {
                "type": "object",
                "properties": {"record_id": {"type": "string"}},
                "required": ["record_id"],
                "additionalProperties": False,
            }
        ),
        on_invoke_tool=_unused_tool,
        needs_approval=needs_approval,
        strict_json_schema=strict_json_schema,
        timeout_seconds=timeout_seconds,
        timeout_behavior=timeout_behavior,
    )


@pytest.mark.asyncio
async def test_model_call_distinguishes_same_named_tool_contracts() -> None:
    baseline = await _record_one_call(tools=[_function_tool()])
    changed_description = await _record_one_call(
        tools=[_function_tool(description="Delete a record.")]
    )
    changed_schema = await _record_one_call(
        tools=[
            _function_tool(
                schema={
                    "type": "object",
                    "properties": {"record_id": {"type": "integer"}},
                    "required": ["record_id"],
                    "additionalProperties": False,
                }
            )
        ]
    )
    changed_approval = await _record_one_call(tools=[_function_tool(needs_approval=True)])
    changed_strictness = await _record_one_call(tools=[_function_tool(strict_json_schema=False)])
    changed_timeout = await _record_one_call(
        tools=[_function_tool(timeout_seconds=9, timeout_behavior="raise_exception")]
    )

    assert baseline != changed_description
    assert baseline != changed_schema
    assert baseline != changed_approval
    assert baseline != changed_strictness
    assert baseline != changed_timeout
    contract = baseline.tools[0]
    assert contract["name"] == "lookup"
    assert contract["description"] == "Look up a record."
    assert contract["approval_policy"] == {"kind": "static", "value": False}


@pytest.mark.parametrize("difference", ["description", "schema", "approval", "timeout"])
@pytest.mark.asyncio
async def test_stream_parity_observes_same_named_tool_contract_changes(difference: str) -> None:
    def build(variant: RunVariant) -> ScenarioCase:
        changed = variant is RunVariant.STREAMING
        kwargs: dict[str, Any] = {}
        if difference == "description" and changed:
            kwargs["description"] = "Delete a record."
        elif difference == "schema" and changed:
            kwargs["schema"] = {
                "type": "object",
                "properties": {"record_id": {"type": "integer"}},
                "required": ["record_id"],
                "additionalProperties": False,
            }
        elif difference == "approval" and changed:
            kwargs["needs_approval"] = True
        elif difference == "timeout" and changed:
            kwargs["timeout_seconds"] = 9
            kwargs["timeout_behavior"] = "raise_exception"
        model = DeterministicModel([[assistant_message("done")]])
        return ScenarioCase(
            agent=Agent(name="contract agent", model=model, tools=[_function_tool(**kwargs)]),
            input="public input",
            model=model,
        )

    proof = await run_scenario(
        Scenario(
            scenario_id=f"model-contract-{difference}",
            revision=1,
            description="Model-call parity must compare complete tool contracts.",
            variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
            invariants=("execution_outcome", "stream_parity", "model_script_consumed"),
            factory=build,
            public_payloads=True,
        )
    )

    assert proof.status == "FAIL"
    parity = proof.invariant_results[1]
    assert parity.reason == "VARIANT_MISMATCH"
    assert "model_calls" in parity.details["differing_fields"]
    assert (
        proof.observations[RunVariant.NON_STREAMING].model_calls
        != proof.observations[RunVariant.STREAMING].model_calls
    )


@pytest.mark.asyncio
async def test_private_observation_hashes_the_complete_model_call_contract() -> None:
    def build(variant: RunVariant) -> ScenarioCase:
        del variant
        model = DeterministicModel([[assistant_message("done")]])
        return ScenarioCase(
            agent=Agent(
                name="private contract agent",
                model=model,
                tools=[_function_tool(description="SECRET-TOOL-DESCRIPTION")],
            ),
            input="private input",
            model=model,
        )

    proof = await run_scenario(
        Scenario(
            scenario_id="private-model-contract",
            revision=1,
            description="Private model contracts are redacted as one canonical payload.",
            variants=(RunVariant.NON_STREAMING,),
            invariants=("execution_outcome", "model_script_consumed"),
            factory=build,
            public_payloads=False,
        )
    )

    model_call = proof.observations[RunVariant.NON_STREAMING].model_calls[0]
    assert isinstance(model_call, dict)
    assert model_call["redacted"] is True
    assert set(model_call) == {"bytes", "kind", "redacted", "sha256"}
    assert "SECRET-TOOL-DESCRIPTION" not in repr(model_call)


@pytest.mark.asyncio
async def test_dynamic_approval_policy_is_stable_and_never_invoked_while_recording() -> None:
    calls = 0

    async def first_policy(_context: Any, _arguments: dict[str, Any], _call_id: str) -> bool:
        nonlocal calls
        calls += 1
        return True

    async def second_policy(_context: Any, _arguments: dict[str, Any], _call_id: str) -> bool:
        nonlocal calls
        calls += 1
        return False

    first = await _record_one_call(tools=[_function_tool(needs_approval=first_policy)])
    second = await _record_one_call(tools=[_function_tool(needs_approval=second_policy)])

    assert calls == 0
    assert first == second
    assert first.tools[0]["approval_policy"] == {"kind": "dynamic_callable"}


@pytest.mark.asyncio
async def test_equivalent_tool_contracts_are_stable_across_object_and_key_order() -> None:
    first = await _record_one_call(tools=[_function_tool()])
    second = await _record_one_call(
        tools=[
            _function_tool(
                schema={
                    "required": ["record_id"],
                    "properties": {"record_id": {"type": "string"}},
                    "additionalProperties": False,
                    "type": "object",
                }
            )
        ]
    )

    assert first == second


async def _unused_handoff(_context: Any, _arguments: str) -> Agent[Any]:
    return Agent(name="target")


def _handoff(*, description: str = "Transfer to target.") -> Handoff[Any, Agent[Any]]:
    return Handoff(
        tool_name="transfer_to_target",
        tool_description=description,
        input_json_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        on_invoke_handoff=_unused_handoff,
        agent_name="target",
    )


@pytest.mark.asyncio
async def test_model_call_captures_handoff_contract() -> None:
    baseline = await _record_one_call(handoffs=[_handoff()])
    changed = await _record_one_call(handoffs=[_handoff(description="Transfer elsewhere.")])

    assert baseline != changed
    assert baseline.handoffs[0]["tool_name"] == "transfer_to_target"
    assert baseline.handoffs[0]["input_json_schema"] == {
        "additionalProperties": False,
        "properties": {},
        "required": [],
        "type": "object",
    }


class _StructuredOutput(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_model_call_captures_output_json_schema_and_strictness() -> None:
    strict = await _record_one_call(
        output_schema=AgentOutputSchema(_StructuredOutput, strict_json_schema=True)
    )
    non_strict = await _record_one_call(
        output_schema=AgentOutputSchema(_StructuredOutput, strict_json_schema=False)
    )

    assert strict != non_strict
    assert strict.output_schema is not None
    assert strict.output_schema["plain_text"] is False
    assert strict.output_schema["strict_json_schema"] is True
    assert strict.output_schema["json_schema"] is not None
