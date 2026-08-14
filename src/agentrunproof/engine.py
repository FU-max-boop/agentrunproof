from __future__ import annotations

import builtins
import inspect
import json
from dataclasses import dataclass, replace
from typing import Any, cast

from agents import RunConfig, Runner, RunResult, RunResultStreaming, RunState

from ._canonical import JsonValue, sha256_hex
from .invariants import InvariantResult, evaluate_invariants, overall_status
from .observation import (
    Observation,
    PhaseObservation,
    _payload,
    aggregate_observation,
    capture_failure_observation,
    capture_observation,
)
from .scenario import (
    DecisionAction,
    ExpectedOutcome,
    LiteralInput,
    PhaseContract,
    ResumeInput,
    RunVariant,
    Scenario,
    ScenarioCase,
    ScenarioPhase,
    ScenarioPlan,
)


class ScenarioTransitionError(RuntimeError):
    """Raised when a declared phase transition cannot be applied exactly."""


RuntimeResult = RunResult | RunResultStreaming


@dataclass(frozen=True)
class ProofRun:
    scenario: Scenario
    observations: dict[RunVariant, Observation]
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]]
    invariant_results: tuple[InvariantResult, ...]
    status: str


async def run_scenario(scenario: Scenario) -> ProofRun:
    observations: dict[RunVariant, Observation] = {}
    contracts: dict[RunVariant, tuple[PhaseContract, ...]] = {}
    for variant in scenario.variants:
        plan = _as_plan(scenario, scenario.factory(variant))
        contracts[variant] = tuple(
            _phase_contract(phase, public_payloads=scenario.public_payloads)
            for phase in plan.phases
        )
        observations[variant] = await _run_plan_variant(
            scenario=scenario,
            variant=variant,
            plan=plan,
        )
    invariant_results = evaluate_invariants(scenario, observations, contracts)
    return ProofRun(
        scenario=scenario,
        observations=observations,
        phase_contracts=contracts,
        invariant_results=invariant_results,
        status=overall_status(invariant_results),
    )


def _as_plan(scenario: Scenario, value: ScenarioCase | ScenarioPlan) -> ScenarioPlan:
    if isinstance(value, ScenarioPlan):
        return value
    if not isinstance(value, ScenarioCase):
        raise TypeError("A scenario factory must return ScenarioCase or ScenarioPlan.")
    return ScenarioPlan(
        phases=(
            ScenarioPhase(
                phase_id="main",
                agent=value.agent,
                input=LiteralInput(value.input),
                model=value.model,
                session=value.session,
                run_config=value.run_config,
                max_turns=value.max_turns,
                tool_counts=value.tool_counts,
                context=value.context,
                expected_outcome=scenario.expected_outcome,
                expected_tool_counts_delta=scenario.expected_tool_counts,
                model_group="legacy",
            ),
        )
    )


def _phase_contract(phase: ScenarioPhase, *, public_payloads: bool) -> PhaseContract:
    resume = phase.input if isinstance(phase.input, ResumeInput) else None
    expected = phase.expected_outcome
    expected_exception = _qualified_expected_exception(expected.exception_type)
    return PhaseContract(
        phase_id=phase.phase_id,
        input_kind="resume" if resume is not None else "literal",
        source_phase=resume.source_phase if resume is not None else None,
        json_round_trip=resume.json_round_trip if resume is not None else False,
        decisions=tuple(
            {
                "action": decision.action.value,
                "call_id_sha256": sha256_hex(decision.call_id),
                "rejection_message": _payload(
                    decision.rejection_message,
                    public=public_payloads,
                ),
            }
            for decision in (resume.decisions if resume is not None else ())
        ),
        expected_outcome=ExpectedOutcome(
            kind=expected.kind,
            interruption_count=expected.interruption_count,
            exception_type=expected_exception,
        ),
        expected_tool_counts_delta=dict(sorted(phase.expected_tool_counts_delta.items())),
        expected_probes_after={
            probe.name: _payload(probe.expected_after, public=public_payloads)
            for probe in sorted(phase.probes, key=lambda item: item.name)
        },
        callback_markers={
            "before": _callback_marker(phase.before),
            "probes": {
                probe.name: _callback_marker(probe.capture)
                for probe in sorted(phase.probes, key=lambda item: item.name)
            },
        },
        model_group=phase.model_group,
    )


def _qualified_expected_exception(value: str | None) -> str | None:
    if value is None or "." in value:
        return value
    candidate = getattr(builtins, value, None)
    if isinstance(candidate, type) and issubclass(candidate, BaseException):
        return f"{candidate.__module__}.{candidate.__qualname__}"
    return value


def _callback_marker(callback: Any) -> str | None:
    if callback is None:
        return None
    module = getattr(callback, "__module__", type(callback).__module__)
    qualname = getattr(callback, "__qualname__", type(callback).__qualname__)
    return f"{module}.{qualname}"


async def _run_plan_variant(
    *,
    scenario: Scenario,
    variant: RunVariant,
    plan: ScenarioPlan,
) -> Observation:
    phase_results: dict[str, RuntimeResult | None] = {}
    phase_observations: list[PhaseObservation] = []
    for phase in plan.phases:
        observation, result = await _run_phase(
            scenario=scenario,
            variant=variant,
            phase=phase,
            phase_results=phase_results,
        )
        phase_results[phase.phase_id] = result
        phase_observations.append(observation)
    return aggregate_observation(variant=variant, phases=phase_observations)


async def _run_phase(
    *,
    scenario: Scenario,
    variant: RunVariant,
    phase: ScenarioPhase,
    phase_results: dict[str, RuntimeResult | None],
) -> tuple[PhaseObservation, RuntimeResult | None]:
    result: RuntimeResult | None = None
    error: BaseException | None = None
    error_origin = "runner"
    stream_event_types: list[str] = []
    transition = _empty_transition(phase)

    try:
        if phase.before is not None:
            hook_result = phase.before()
            if inspect.isawaitable(hook_result):
                await hook_result
    except Exception as caught:
        error = caught
        error_origin = "transition"

    model_call_start = len(phase.model.calls)
    session_items_before = phase.session.snapshot() if phase.session is not None else None
    session_operation_start = len(phase.session.operations) if phase.session is not None else 0
    tool_counts_before = dict(phase.tool_counts)

    try:
        probes_before = _capture_probes(phase)
    except Exception as probe_error:
        return (
            capture_failure_observation(
                phase=phase,
                stream_event_types=stream_event_types,
                error=probe_error,
                state_transition=transition,
            ),
            None,
        )

    input_value: Any = None
    if error is None:
        try:
            input_value, transition = await _resolve_input(
                phase=phase,
                phase_results=phase_results,
            )
        except Exception as caught:
            error = caught
            error_origin = "transition"

    run_config = (
        RunConfig(tracing_disabled=True)
        if phase.run_config is None
        else replace(phase.run_config, tracing_disabled=True)
    )
    if error is None:
        try:
            result, stream_event_types = await _invoke_runner(
                variant=variant,
                phase=phase,
                input_value=input_value,
                run_config=run_config,
            )
        except _StreamingRunError as caught:
            result = caught.result
            stream_event_types = caught.stream_event_types
            error = caught.error
        except Exception as caught:
            error = caught

    try:
        probes_after = _capture_probes(phase)
        observation = capture_observation(
            variant=variant,
            phase=phase,
            result=result,
            stream_event_types=stream_event_types,
            error=error,
            error_origin=error_origin,
            public_payloads=scenario.public_payloads,
            model_call_start=model_call_start,
            session_items_before=session_items_before,
            session_operation_start=session_operation_start,
            tool_counts_before=tool_counts_before,
            state_transition=transition,
            probes_before=probes_before,
            probes_after=probes_after,
        )
        if not isinstance(observation, PhaseObservation):
            raise TypeError("The observation normalizer returned an invalid phase observation.")
        return observation, result
    except Exception as observation_error:
        return (
            capture_failure_observation(
                phase=phase,
                stream_event_types=stream_event_types,
                error=observation_error,
                state_transition=transition,
            ),
            result,
        )


@dataclass(frozen=True)
class _StreamingRunError(Exception):
    error: BaseException
    result: RunResultStreaming
    stream_event_types: list[str]


async def _invoke_runner(
    *,
    variant: RunVariant,
    phase: ScenarioPhase,
    input_value: Any,
    run_config: RunConfig,
) -> tuple[RuntimeResult, list[str]]:
    kwargs: dict[str, Any] = {
        "context": phase.context,
        "session": phase.session,
        "run_config": run_config,
    }
    if phase.max_turns is not None:
        kwargs["max_turns"] = phase.max_turns
    if variant is RunVariant.NON_STREAMING:
        return await Runner.run(phase.agent, input_value, **kwargs), []
    if variant is RunVariant.STREAMING:
        streamed = Runner.run_streamed(phase.agent, input_value, **kwargs)
        stream_event_types: list[str] = []
        try:
            async for event in streamed.stream_events():
                stream_event_types.append(getattr(event, "type", type(event).__name__))
        except Exception as error:
            raise _StreamingRunError(
                error=error,
                result=streamed,
                stream_event_types=stream_event_types,
            ) from error
        return streamed, stream_event_types
    raise ValueError(f"Unsupported runtime variant: {variant}")


async def _resolve_input(
    *,
    phase: ScenarioPhase,
    phase_results: dict[str, RuntimeResult | None],
) -> tuple[Any, dict[str, JsonValue]]:
    if isinstance(phase.input, LiteralInput):
        return phase.input.value, _empty_transition(phase)

    resume = phase.input
    source_result = phase_results.get(resume.source_phase)
    if source_result is None:
        raise ScenarioTransitionError(
            f"Resume source phase {resume.source_phase!r} did not produce a RunResult."
        )
    source_state = source_result.to_state()
    source_ids = [_raw_interruption_call_id(item) for item in source_state.get_interruptions()]
    round_trip_equal: bool | None = None
    restored_state_equal: bool | None = None
    state_schema_version: str | None = None
    if resume.json_round_trip:
        state_json = source_state.to_json()
        encoded = json.dumps(
            state_json,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        restored_json = json.loads(encoded)
        round_trip_equal = restored_json == state_json
        raw_schema_version = restored_json.get("$schemaVersion")
        state_schema_version = raw_schema_version if isinstance(raw_schema_version, str) else None
        state = await RunState.from_json(phase.agent, restored_json)
        restored_state_equal = state.to_json() == state_json
    else:
        state = source_state
    restored = list(state.get_interruptions())
    restored_ids = [_raw_interruption_call_id(item) for item in restored]
    actual_decisions: list[dict[str, JsonValue]] = []
    for decision in resume.decisions:
        matches = [
            interruption
            for interruption, call_id in zip(restored, restored_ids, strict=True)
            if call_id == decision.call_id
        ]
        matched = len(matches) == 1
        actual_decisions.append(
            {
                "action": decision.action.value,
                "call_id_sha256": sha256_hex(decision.call_id),
                "matched": matched,
            }
        )
        if not matched:
            raise ScenarioTransitionError(
                f"Decision call_id {decision.call_id!r} did not match exactly one interruption."
            )
        if decision.action is DecisionAction.APPROVE:
            state.approve(matches[0])
        else:
            state.reject(matches[0], rejection_message=decision.rejection_message)
    transition: dict[str, JsonValue] = {
        "kind": "resume",
        "source_phase": resume.source_phase,
        "json_round_trip_requested": resume.json_round_trip,
        "json_round_trip_equal": round_trip_equal,
        "restored_state_equal": restored_state_equal,
        "state_schema_version": state_schema_version,
        "source_interruption_call_ids": [_identifier_digest(value) for value in source_ids],
        "restored_interruption_call_ids": [_identifier_digest(value) for value in restored_ids],
        "decisions": cast(list[JsonValue], actual_decisions),
    }
    return state, transition


def _empty_transition(phase: ScenarioPhase) -> dict[str, JsonValue]:
    kind = "resume" if isinstance(phase.input, ResumeInput) else "literal"
    source = phase.input.source_phase if isinstance(phase.input, ResumeInput) else None
    requested = phase.input.json_round_trip if isinstance(phase.input, ResumeInput) else False
    return {
        "kind": kind,
        "source_phase": source,
        "json_round_trip_requested": requested,
        "json_round_trip_equal": None,
        "restored_state_equal": None,
        "state_schema_version": None,
        "source_interruption_call_ids": [],
        "restored_interruption_call_ids": [],
        "decisions": [],
    }


def _raw_interruption_call_id(interruption: Any) -> str | None:
    raw_item = getattr(interruption, "raw_item", None)
    if isinstance(raw_item, dict):
        call_id = raw_item.get("call_id") or raw_item.get("id")
    else:
        call_id = getattr(raw_item, "call_id", None) or getattr(raw_item, "id", None)
    return call_id if isinstance(call_id, str) and call_id else None


def _identifier_digest(value: str | None) -> str:
    return sha256_hex(value) if value is not None else "INVALID"


def _capture_probes(phase: ScenarioPhase) -> dict[str, Any]:
    return {probe.name: probe.capture() for probe in phase.probes}
