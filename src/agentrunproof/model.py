from __future__ import annotations

import copy
import importlib
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import (
    ModelResponse,
    TResponseInputItem,
    TResponseOutputItem,
    TResponseStreamEvent,
)
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.tool import FunctionTool, Tool
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_prompt_param import ResponsePromptParam
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from ._canonical import JsonValue, deep_json_copy


class ModelScriptError(RuntimeError):
    """Base error for an invalid or incompletely consumed deterministic model script."""


class UnexpectedModelCall(ModelScriptError):
    """Raised when the runner calls the model after every scripted step was consumed."""


class UnconsumedModelSteps(ModelScriptError):
    """Raised when a run finishes before consuming every scripted model step."""


class UnsupportedToolContract(ModelScriptError):
    """Raised when a tool outside the v0.1 function-tool contract reaches the model."""


@dataclass(frozen=True)
class ModelCall:
    system_instructions: str | None
    input: Any
    model_settings: dict[str, Any]
    tools: tuple[dict[str, JsonValue], ...]
    handoffs: tuple[dict[str, JsonValue], ...]
    output_schema: dict[str, JsonValue] | None
    previous_response_id: str | None
    conversation_id: str | None
    prompt: Any
    streamed: bool


@dataclass(frozen=True)
class ModelStep:
    output: tuple[TResponseOutputItem, ...] = ()
    error: Exception | None = None
    usage: Usage = field(default_factory=lambda: Usage(requests=1))
    response_id: str = "agentrunproof-response"

    def __post_init__(self) -> None:
        if self.error is not None and self.output:
            raise ValueError("A model step cannot combine output and an error.")


class DeterministicModel(Model):
    """A narrow public-``Model`` test double used by AgentRunProof scenarios."""

    def __init__(self, steps: Sequence[ModelStep | Sequence[TResponseOutputItem]]) -> None:
        normalized_steps = [
            step if isinstance(step, ModelStep) else ModelStep(output=tuple(step)) for step in steps
        ]
        self._sdk_model = _sdk_scripted_model(normalized_steps)
        self._steps = normalized_steps if self._sdk_model is None else None
        self._calls: list[ModelCall] = []

    @property
    def calls(self) -> tuple[ModelCall, ...]:
        return tuple(copy.deepcopy(self._calls))

    @property
    def remaining_steps(self) -> int:
        if self._sdk_model is not None:
            remaining = self._sdk_model.remaining_steps
            if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining < 0:
                raise ModelScriptError(
                    "agents.testing.ScriptedModel returned an invalid remaining_steps value."
                )
            return remaining
        if self._steps is None:
            raise ModelScriptError("The deterministic model script backend is unavailable.")
        return len(self._steps)

    def assert_complete(self) -> None:
        remaining = self.remaining_steps
        if remaining:
            raise UnconsumedModelSteps(f"{remaining} scripted model step(s) remain.")

    def _next_step(self) -> ModelStep:
        if self._steps is None:
            if self.remaining_steps == 0:
                raise UnexpectedModelCall("The runner made an unexpected model call.")
            raise ModelScriptError(
                "Direct step access is unavailable with agents.testing.ScriptedModel."
            )
        if not self._steps:
            raise UnexpectedModelCall("The runner made an unexpected model call.")
        return self._steps.pop(0)

    def _record_call(
        self,
        *,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
        streamed: bool,
    ) -> None:
        self._calls.append(
            ModelCall(
                system_instructions=system_instructions,
                input=copy.deepcopy(input),
                model_settings=copy.deepcopy(model_settings.to_json_dict()),
                tools=tuple(_function_tool_contract(tool) for tool in tools),
                handoffs=tuple(_handoff_contract(handoff) for handoff in handoffs),
                output_schema=_output_schema_contract(output_schema),
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=copy.deepcopy(prompt),
                streamed=streamed,
            )
        )

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        self._record_call(
            system_instructions=system_instructions,
            input=input,
            model_settings=model_settings,
            tools=tools,
            output_schema=output_schema,
            handoffs=handoffs,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
            streamed=False,
        )
        if self.remaining_steps == 0:
            raise UnexpectedModelCall("The runner made an unexpected model call.")
        if self._sdk_model is not None:
            return cast(
                ModelResponse,
                await self._sdk_model.get_response(
                    system_instructions,
                    input,
                    model_settings,
                    tools,
                    output_schema,
                    handoffs,
                    tracing,
                    previous_response_id=previous_response_id,
                    conversation_id=conversation_id,
                    prompt=prompt,
                ),
            )
        del tracing
        step = self._next_step()
        if step.error is not None:
            raise step.error
        return ModelResponse(
            output=copy.deepcopy(list(step.output)),
            usage=copy.deepcopy(step.usage),
            response_id=step.response_id,
        )

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        self._record_call(
            system_instructions=system_instructions,
            input=input,
            model_settings=model_settings,
            tools=tools,
            output_schema=output_schema,
            handoffs=handoffs,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
            streamed=True,
        )
        if self.remaining_steps == 0:
            raise UnexpectedModelCall("The runner made an unexpected model call.")
        if self._sdk_model is not None:
            async for event in self._sdk_model.stream_response(
                system_instructions,
                input,
                model_settings,
                tools,
                output_schema,
                handoffs,
                tracing,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=prompt,
            ):
                yield event
            return
        del tracing
        step = self._next_step()
        if step.error is not None:
            raise step.error
        for event in _terminal_events_for_step(step):
            yield event


def _sdk_scripted_model(steps: Sequence[ModelStep]) -> Any | None:
    """Use the SDK's public scripted model when that released API is available."""

    try:
        testing = importlib.import_module("agents.testing")
    except ModuleNotFoundError as error:
        if error.name == "agents.testing":
            return None
        raise ModelScriptError(
            "The installed Agents SDK exposes agents.testing but one of its dependencies "
            "could not be imported."
        ) from error
    scripted_model = getattr(testing, "ScriptedModel", None)
    if not isinstance(scripted_model, type):
        raise ModelScriptError(
            "The installed Agents SDK does not expose agents.testing.ScriptedModel."
        )
    sdk_steps: list[Any] = []
    for step in steps:
        if step.error is not None:
            sdk_steps.append(step.error)
            continue
        sdk_steps.append(
            {
                "output": copy.deepcopy(step.output),
                "usage": copy.deepcopy(step.usage),
                "response_id": step.response_id,
                "stream_events": _terminal_events_for_step(step),
            }
        )
    try:
        return scripted_model(sdk_steps, emit_traces=False)
    except (TypeError, ValueError) as error:
        raise ModelScriptError(
            "The installed agents.testing.ScriptedModel API is incompatible with AgentRunProof."
        ) from error


def _terminal_events_for_step(step: ModelStep) -> tuple[TResponseStreamEvent, ...]:
    output = copy.deepcopy(list(step.output))
    events: list[TResponseStreamEvent] = []
    sequence_number = 0
    for output_index, item in enumerate(output):
        events.append(
            cast(
                TResponseStreamEvent,
                ResponseOutputItemDoneEvent(
                    type="response.output_item.done",
                    item=item,
                    output_index=output_index,
                    sequence_number=sequence_number,
                ),
            )
        )
        sequence_number += 1
    events.append(
        cast(
            TResponseStreamEvent,
            ResponseCompletedEvent(
                type="response.completed",
                response=_response_for_step(step, output),
                sequence_number=sequence_number,
            ),
        )
    )
    return tuple(events)


def _function_tool_contract(tool: Tool) -> dict[str, JsonValue]:
    """Capture the public, model-visible function-tool contract without invoking callbacks."""

    if not isinstance(tool, FunctionTool):
        tool_type = f"{type(tool).__module__}.{type(tool).__qualname__}"
        raise UnsupportedToolContract(
            f"AgentRunProof v0.1 supports FunctionTool model contracts, got {tool_type}."
        )
    return _contract_object(
        {
            "kind": "function",
            "name": tool.name,
            "qualified_name": tool.qualified_name,
            "description": tool.description,
            "params_json_schema": tool.params_json_schema,
            "strict_json_schema": tool.strict_json_schema,
            "approval_policy": _boolean_or_callable_policy(tool.needs_approval),
            "enabled_policy": _boolean_or_callable_policy(tool.is_enabled),
            "tool_input_guardrails": [
                _tool_guardrail_contract(guardrail)
                for guardrail in tool.tool_input_guardrails or []
            ],
            "tool_output_guardrails": [
                _tool_guardrail_contract(guardrail)
                for guardrail in tool.tool_output_guardrails or []
            ],
            "timeout_seconds": tool.timeout_seconds,
            "timeout_behavior": tool.timeout_behavior,
            "timeout_error_function": _optional_callable_marker(tool.timeout_error_function),
            "defer_loading": tool.defer_loading,
            "custom_data_extractor": _optional_callable_marker(tool.custom_data_extractor),
            "allowed_callers": tool.allowed_callers,
            "output_json_schema": tool.output_json_schema,
        },
        label="function tool contract",
    )


def _handoff_contract(handoff: Handoff[Any, Any]) -> dict[str, JsonValue]:
    """Capture the public handoff contract without invoking routing or filter callbacks."""

    return _contract_object(
        {
            "tool_name": handoff.tool_name,
            "tool_description": handoff.tool_description,
            "input_json_schema": handoff.input_json_schema,
            "strict_json_schema": handoff.strict_json_schema,
            "agent_name": handoff.agent_name,
            "nest_handoff_history": handoff.nest_handoff_history,
            "enabled_policy": _boolean_or_callable_policy(handoff.is_enabled),
            "input_filter": _optional_callable_marker(handoff.input_filter),
        },
        label="handoff contract",
    )


def _output_schema_contract(
    output_schema: AgentOutputSchemaBase | None,
) -> dict[str, JsonValue] | None:
    if output_schema is None:
        return None
    plain_text = output_schema.is_plain_text()
    return _contract_object(
        {
            "name": output_schema.name(),
            "plain_text": plain_text,
            "json_schema": None if plain_text else output_schema.json_schema(),
            "strict_json_schema": output_schema.is_strict_json_schema(),
        },
        label="output schema contract",
    )


def _boolean_or_callable_policy(value: bool | Callable[..., Any]) -> dict[str, JsonValue]:
    if isinstance(value, bool):
        return {"kind": "static", "value": value}
    if callable(value):
        return {"kind": "dynamic_callable"}
    raise ModelScriptError(
        f"A model contract policy must be a bool or callable; got {type(value).__name__}."
    )


def _optional_callable_marker(value: Callable[..., Any] | None) -> str:
    if value is None:
        return "none"
    if callable(value):
        return "dynamic_callable"
    raise ModelScriptError(
        f"An optional model contract callback must be callable or None; got {type(value).__name__}."
    )


def _tool_guardrail_contract(value: Any) -> dict[str, JsonValue]:
    name = getattr(value, "name", None)
    callback = getattr(value, "guardrail_function", None)
    if name is not None and not isinstance(name, str):
        raise ModelScriptError("A tool guardrail name must be a string or None.")
    return {
        "name": name,
        "guardrail_function": _optional_callable_marker(callback),
    }


def _contract_object(value: Mapping[str, Any], *, label: str) -> dict[str, JsonValue]:
    normalized = deep_json_copy(value)
    if not isinstance(normalized, dict):
        raise ModelScriptError(f"The normalized {label} must be a JSON object.")
    return normalized


def assistant_message(text: str, *, item_id: str = "agentrunproof-message") -> TResponseOutputItem:
    return ResponseOutputMessage(
        id=item_id,
        type="message",
        role="assistant",
        status="completed",
        content=[
            ResponseOutputText(
                text=text,
                type="output_text",
                annotations=[],
                logprobs=[],
            )
        ],
    )


def function_call(
    name: str,
    arguments: str | Mapping[str, Any],
    *,
    call_id: str,
    item_id: str = "agentrunproof-tool-call",
) -> TResponseOutputItem:
    serialized = (
        arguments
        if isinstance(arguments, str)
        else json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    return ResponseFunctionToolCall(
        id=item_id,
        call_id=call_id,
        type="function_call",
        name=name,
        arguments=serialized,
    )


def _response_for_step(step: ModelStep, output: list[TResponseOutputItem]) -> Response:
    usage = step.usage
    return Response(
        id=step.response_id,
        created_at=0,
        model="agentrunproof-deterministic-model",
        object="response",
        output=output,
        parallel_tool_calls=False,
        status="completed",
        tool_choice="none",
        tools=[],
        top_p=None,
        usage=ResponseUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            input_tokens_details=InputTokensDetails.model_validate(
                {
                    "cached_tokens": getattr(usage.input_tokens_details, "cached_tokens", 0),
                    "cache_write_tokens": getattr(
                        usage.input_tokens_details, "cache_write_tokens", 0
                    ),
                }
            ),
            output_tokens_details=OutputTokensDetails(
                reasoning_tokens=getattr(usage.output_tokens_details, "reasoning_tokens", 0)
            ),
        ),
    )
