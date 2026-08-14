from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, cast

from agents import RunResult, RunResultStreaming

from ._canonical import JsonValue, canonical_bytes, sha256_hex, to_json_value
from .scenario import LiteralInput, RunVariant, ScenarioPhase

NORMALIZER_REVISION = 1
RuntimeResult = RunResult | RunResultStreaming


@dataclass(frozen=True)
class PhaseObservation:
    phase_id: str
    model_group: str
    input_kind: str
    status: str
    final_output: JsonValue
    last_agent: str | None
    new_items: list[JsonValue]
    model_calls: list[JsonValue]
    session_items_before: list[JsonValue] | None
    session_items_after: list[JsonValue] | None
    session_operations: list[JsonValue]
    tool_counts_delta: dict[str, int]
    tool_linkage: dict[str, JsonValue]
    interruption_call_ids: list[str]
    interruption_count: int
    usage: JsonValue
    guardrails: JsonValue
    stream_event_types: list[str]
    remaining_model_steps: int
    exception: dict[str, JsonValue] | None
    state_transition: dict[str, JsonValue]
    probes_before: dict[str, JsonValue]
    probes_after: dict[str, JsonValue]

    def as_json(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], to_json_value(self.__dict__))


@dataclass(frozen=True)
class Observation:
    variant: RunVariant
    status: str
    final_output: JsonValue
    last_agent: str | None
    new_items: list[JsonValue]
    model_calls: list[JsonValue]
    session_items: list[JsonValue] | None
    session_operations: list[JsonValue]
    tool_counts: dict[str, int]
    tool_linkage: dict[str, JsonValue]
    interruption_call_ids: list[str]
    interruption_count: int
    usage: JsonValue
    guardrails: JsonValue
    stream_event_types: list[str]
    remaining_model_steps: int
    exception: dict[str, JsonValue] | None
    phases: tuple[PhaseObservation, ...]

    def as_json(self) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            to_json_value(
                {
                    "variant": self.variant.value,
                    "status": self.status,
                    "final_output": self.final_output,
                    "last_agent": self.last_agent,
                    "new_items": self.new_items,
                    "model_calls": self.model_calls,
                    "session_items": self.session_items,
                    "session_operations": self.session_operations,
                    "tool_counts": self.tool_counts,
                    "tool_linkage": self.tool_linkage,
                    "interruption_call_ids": self.interruption_call_ids,
                    "interruption_count": self.interruption_count,
                    "usage": self.usage,
                    "guardrails": self.guardrails,
                    "stream_event_types": self.stream_event_types,
                    "remaining_model_steps": self.remaining_model_steps,
                    "exception": self.exception,
                    "phases": [phase.as_json() for phase in self.phases],
                }
            ),
        )


def capture_observation(
    *,
    variant: RunVariant,
    phase: ScenarioPhase,
    result: RuntimeResult | None,
    stream_event_types: list[str],
    error: BaseException | None,
    error_origin: str,
    public_payloads: bool,
    model_call_start: int,
    session_items_before: list[JsonValue] | None,
    session_operation_start: int,
    tool_counts_before: dict[str, int],
    state_transition: dict[str, JsonValue],
    probes_before: dict[str, Any],
    probes_after: dict[str, Any],
) -> PhaseObservation:
    new_items = [] if result is None else [_run_item_json(item) for item in result.new_items]
    session_items = phase.session.snapshot() if phase.session is not None else None
    model_calls = phase.model.calls[model_call_start:]
    tool_counts_delta = {
        name: count - tool_counts_before.get(name, 0)
        for name, count in sorted(phase.tool_counts.items())
        if count - tool_counts_before.get(name, 0) != 0 or name in phase.expected_tool_counts_delta
    }
    tool_linkage = cast(
        dict[str, JsonValue],
        to_json_value(
            {
                "generated": _run_item_tool_linkage(result),
                "session_before": (
                    _tool_linkage(session_items_before)
                    if session_items_before is not None
                    else None
                ),
                "session_after": (
                    _tool_linkage(session_items) if session_items is not None else None
                ),
                "model_inputs": [
                    _tool_linkage(call.input if isinstance(call.input, list) else [])
                    for call in model_calls
                ],
            }
        ),
    )
    return PhaseObservation(
        phase_id=phase.phase_id,
        model_group=phase.model_group,
        input_kind="literal" if isinstance(phase.input, LiteralInput) else "resume",
        status="ERROR" if error is not None else "PASS",
        final_output=_payload(getattr(result, "final_output", None), public=public_payloads),
        last_agent=_last_agent_name(result),
        new_items=[_payload(item, public=public_payloads) for item in new_items],
        model_calls=[
            _payload(
                {
                    "system_instructions": call.system_instructions,
                    "input": call.input,
                    "model_settings": call.model_settings,
                    "tools": list(call.tools),
                    "handoffs": list(call.handoffs),
                    "output_schema": call.output_schema,
                    "previous_response_id": call.previous_response_id,
                    "conversation_id": call.conversation_id,
                    "prompt": call.prompt,
                },
                public=public_payloads,
            )
            for call in model_calls
        ],
        session_items_before=(
            [_payload(item, public=public_payloads) for item in session_items_before]
            if session_items_before is not None
            else None
        ),
        session_items_after=(
            [_payload(item, public=public_payloads) for item in session_items]
            if session_items is not None
            else None
        ),
        session_operations=(
            [
                to_json_value(operation)
                for operation in phase.session.operations[session_operation_start:]
            ]
            if phase.session is not None
            else []
        ),
        tool_counts_delta=tool_counts_delta,
        tool_linkage=tool_linkage,
        interruption_call_ids=_interruption_call_ids(result),
        interruption_count=len(getattr(result, "interruptions", []) or []),
        usage=_usage_json(result),
        guardrails=_guardrail_json(result, public=public_payloads),
        stream_event_types=list(stream_event_types),
        remaining_model_steps=phase.model.remaining_steps,
        exception=_exception_json(error, origin=error_origin),
        state_transition=state_transition,
        probes_before={
            name: _payload(value, public=public_payloads)
            for name, value in sorted(probes_before.items())
        },
        probes_after={
            name: _payload(value, public=public_payloads)
            for name, value in sorted(probes_after.items())
        },
    )


def capture_failure_observation(
    *,
    phase: ScenarioPhase,
    stream_event_types: list[str],
    error: BaseException,
    state_transition: dict[str, JsonValue],
) -> PhaseObservation:
    """Return a minimal fail-closed observation when normalization itself fails."""

    return PhaseObservation(
        phase_id=phase.phase_id,
        model_group=phase.model_group,
        input_kind="literal" if isinstance(phase.input, LiteralInput) else "resume",
        status="ERROR",
        final_output=None,
        last_agent=None,
        new_items=[],
        model_calls=[],
        session_items_before=None,
        session_items_after=None,
        session_operations=[],
        tool_counts_delta={},
        tool_linkage={
            "generated": [],
            "session_before": None,
            "session_after": None,
            "model_inputs": [],
        },
        interruption_call_ids=[],
        interruption_count=0,
        usage=None,
        guardrails=None,
        stream_event_types=list(stream_event_types),
        remaining_model_steps=phase.model.remaining_steps,
        exception=_exception_json(error, origin="observation"),
        state_transition=state_transition,
        probes_before={},
        probes_after={},
    )


def aggregate_observation(*, variant: RunVariant, phases: list[PhaseObservation]) -> Observation:
    if not phases:
        raise ValueError("At least one phase observation is required.")
    final = phases[-1]
    tool_counts: dict[str, int] = {}
    for phase in phases:
        for name, count in phase.tool_counts_delta.items():
            tool_counts[name] = tool_counts.get(name, 0) + count
    model_calls = [call for phase in phases for call in phase.model_calls]
    new_items = [item for phase in phases for item in phase.new_items]
    session_operations = [item for phase in phases for item in phase.session_operations]
    generated = [
        event
        for phase in phases
        for event in cast(list[JsonValue], phase.tool_linkage["generated"])
    ]
    model_inputs = [
        events
        for phase in phases
        for events in cast(list[JsonValue], phase.tool_linkage["model_inputs"])
    ]
    final_session_linkage = final.tool_linkage["session_after"]
    return Observation(
        variant=variant,
        status=final.status,
        final_output=final.final_output,
        last_agent=final.last_agent,
        new_items=new_items,
        model_calls=model_calls,
        session_items=final.session_items_after,
        session_operations=session_operations,
        tool_counts=dict(sorted(tool_counts.items())),
        tool_linkage={
            "generated": generated,
            "session": final_session_linkage,
            "model_inputs": model_inputs,
        },
        interruption_call_ids=final.interruption_call_ids,
        interruption_count=final.interruption_count,
        usage=final.usage if len(phases) == 1 else [phase.usage for phase in phases],
        guardrails=(
            final.guardrails if len(phases) == 1 else [phase.guardrails for phase in phases]
        ),
        stream_event_types=[event for phase in phases for event in phase.stream_event_types],
        remaining_model_steps=final.remaining_model_steps,
        exception=final.exception,
        phases=tuple(phases),
    )


def _payload(value: Any, *, public: bool) -> JsonValue:
    payload = to_json_value(value)
    if public:
        return payload
    encoded = canonical_bytes(payload)
    return {
        "redacted": True,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "kind": _json_kind(payload),
    }


def _json_kind(value: JsonValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _run_item_json(item: Any) -> JsonValue:
    if getattr(item, "type", None) == "tool_approval_item":
        raw_item = getattr(item, "raw_item", None)
        if raw_item is None:
            raise TypeError("A tool approval item must expose its raw invocation.")
        return to_json_value(raw_item)
    to_input_item = getattr(item, "to_input_item", None)
    if callable(to_input_item):
        return to_json_value(to_input_item())
    raw_item = getattr(item, "raw_item", None)
    if raw_item is not None:
        return to_json_value(raw_item)
    return to_json_value(item)


def _run_item_tool_linkage(result: RuntimeResult | None) -> list[dict[str, str]]:
    if result is None:
        return []
    items = [
        _run_item_json(item)
        for item in result.new_items
        if getattr(item, "type", None) != "tool_approval_item"
    ]
    return _tool_linkage(items)


def _last_agent_name(result: RuntimeResult | None) -> str | None:
    if result is None:
        return None
    agent = getattr(result, "last_agent", None)
    name = getattr(agent, "name", None)
    return name if isinstance(name, str) and name else None


def _usage_json(result: RuntimeResult | None) -> JsonValue:
    if result is None:
        return None
    wrapper = getattr(result, "context_wrapper", None)
    usage = getattr(wrapper, "usage", None)
    return to_json_value(usage)


def _guardrail_json(result: RuntimeResult | None, *, public: bool) -> JsonValue:
    if result is None:
        return None
    groups: dict[str, JsonValue] = {}
    for attribute in (
        "input_guardrail_results",
        "output_guardrail_results",
        "tool_input_guardrail_results",
        "tool_output_guardrail_results",
    ):
        values = getattr(result, attribute, []) or []
        groups[attribute] = [
            {
                "result_type": type(value).__name__,
                "tripwire_triggered": _tripwire_value(value),
                "output_info": _payload(
                    getattr(getattr(value, "output", None), "output_info", None),
                    public=public,
                ),
            }
            for value in values
        ]
    return groups


def _tripwire_value(value: Any) -> bool | None:
    output = getattr(value, "output", None)
    triggered = getattr(output, "tripwire_triggered", None)
    return triggered if isinstance(triggered, bool) else None


def _exception_json(error: BaseException | None, *, origin: str) -> dict[str, JsonValue] | None:
    if error is None:
        return None
    try:
        message = str(error)
    except BaseException:
        message = "<unrenderable>"
    return {
        "type": f"{type(error).__module__}.{type(error).__qualname__}",
        "message_sha256": hashlib.sha256(message.encode("utf-8", errors="replace")).hexdigest(),
        "origin": origin,
    }


def _tool_linkage(items: list[JsonValue]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "function_call":
            raw_call_id = item.get("call_id") or item.get("id")
            kind = "call"
        elif item_type == "function_call_output":
            raw_call_id = item.get("call_id")
            kind = "output"
        else:
            continue
        if not isinstance(raw_call_id, str) or not raw_call_id:
            events.append({"kind": kind, "call_id_sha256": "INVALID"})
            continue
        events.append({"kind": kind, "call_id_sha256": sha256_hex(raw_call_id)})
    return events


def _interruption_call_ids(result: RuntimeResult | None) -> list[str]:
    if result is None:
        return []
    identifiers: list[str] = []
    for interruption in getattr(result, "interruptions", []) or []:
        raw_item = getattr(interruption, "raw_item", None)
        if isinstance(raw_item, dict):
            call_id = raw_item.get("call_id") or raw_item.get("id")
        else:
            call_id = getattr(raw_item, "call_id", None) or getattr(raw_item, "id", None)
        identifiers.append(
            sha256_hex(call_id) if isinstance(call_id, str) and call_id else "INVALID"
        )
    return identifiers
