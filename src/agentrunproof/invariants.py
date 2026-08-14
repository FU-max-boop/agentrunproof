from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, cast

from ._canonical import JsonValue, to_json_value
from .observation import Observation, PhaseObservation
from .scenario import OutcomeKind, PhaseContract, RunVariant, Scenario

EVALUATOR_REVISION = 1


@dataclass(frozen=True)
class InvariantResult:
    name: str
    status: str
    reason: str
    details: JsonValue

    def as_json(self) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            to_json_value(
                {
                    "name": self.name,
                    "status": self.status,
                    "reason": self.reason,
                    "details": self.details,
                }
            ),
        )


def evaluate_invariants(
    scenario: Scenario,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]] | None = None,
) -> tuple[InvariantResult, ...]:
    evaluators = {
        "execution_outcome": _execution_outcome,
        "stream_parity": _stream_parity,
        "tool_linkage": _tool_linkage,
        "exactly_once": _exactly_once,
        "model_script_consumed": _model_script_consumed,
        "state_transitions": _state_transitions,
        "state_fork_isolation": _state_fork_isolation,
        "phase_contract": _phase_contract,
        "session_replay": _session_replay,
    }
    results: list[InvariantResult] = []
    for name in scenario.invariants:
        evaluator = evaluators.get(name)
        if evaluator is None:
            results.append(
                InvariantResult(
                    name=name,
                    status="NOT_RUN",
                    reason="UNKNOWN_INVARIANT",
                    details={},
                )
            )
        else:
            results.append(evaluator(scenario, observations, phase_contracts))
    return tuple(results)


def _execution_outcome(
    scenario: Scenario,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]] | None,
) -> InvariantResult:
    if not observations:
        return _not_run("execution_outcome", "NO_OBSERVATIONS")
    failures: dict[str, Any] = {}
    for variant, observation in observations.items():
        contracts = phase_contracts.get(variant) if phase_contracts is not None else None
        if contracts is None or len(contracts) != len(observation.phases):
            failures[variant.value] = {"reason": "PHASE_CONTRACT_MISMATCH"}
            continue
        phase_failures: dict[str, Any] = {}
        for phase, contract in zip(observation.phases, contracts, strict=True):
            expected = contract.expected_outcome
            if expected.kind is OutcomeKind.COMPLETED:
                matched = (
                    phase.status == "PASS"
                    and phase.exception is None
                    and phase.interruption_count == 0
                )
            elif expected.kind is OutcomeKind.INTERRUPTED:
                matched = (
                    phase.status == "PASS"
                    and phase.exception is None
                    and phase.interruption_count == expected.interruption_count
                )
            else:
                matched = (
                    phase.status == "ERROR"
                    and phase.exception is not None
                    and phase.exception.get("origin") == "runner"
                    and phase.exception.get("type") == expected.exception_type
                    and phase.interruption_count == 0
                )
            if not matched:
                phase_failures[phase.phase_id] = {
                    "expected": _outcome_json(expected),
                    "status": phase.status,
                    "interruption_count": phase.interruption_count,
                    "exception_type": (
                        phase.exception.get("type") if phase.exception is not None else None
                    ),
                    "exception_origin": (
                        phase.exception.get("origin") if phase.exception is not None else None
                    ),
                }
        if phase_failures:
            failures[variant.value] = phase_failures
    if failures:
        return _fail(
            "execution_outcome",
            "UNEXPECTED_EXECUTION_OUTCOME",
            {
                "variants": failures,
            },
        )
    return _pass(
        "execution_outcome",
        {
            "variants": len(observations),
            "phases": sum(len(observation.phases) for observation in observations.values()),
        },
    )


def _outcome_json(expected: Any) -> dict[str, Any]:
    return {
        "kind": expected.kind.value,
        "interruption_count": expected.interruption_count,
        "exception_type": expected.exception_type,
    }


def overall_status(results: tuple[InvariantResult, ...]) -> str:
    if any(result.status == "FAIL" for result in results):
        return "FAIL"
    if results and all(result.status == "PASS" for result in results):
        return "PASS"
    return "NOT_RUN"


def _stream_parity(
    scenario: Scenario,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]] | None,
) -> InvariantResult:
    del scenario, phase_contracts
    left = observations.get(RunVariant.NON_STREAMING)
    right = observations.get(RunVariant.STREAMING)
    if left is None or right is None:
        return _not_run("stream_parity", "MISSING_VARIANT")
    left_projection = _parity_projection(left)
    right_projection = _parity_projection(right)
    if left_projection == right_projection:
        return _pass("stream_parity", {"compared_fields": sorted(left_projection)})
    differing = sorted(
        key for key in left_projection if left_projection[key] != right_projection.get(key)
    )
    return _fail(
        "stream_parity",
        "VARIANT_MISMATCH",
        {"differing_fields": differing},
    )


def _parity_projection(observation: Observation) -> dict[str, Any]:
    return {
        "status": observation.status,
        "final_output": observation.final_output,
        "last_agent": observation.last_agent,
        "model_calls": observation.model_calls,
        "session_items": observation.session_items,
        "tool_counts": observation.tool_counts,
        "guardrails": (
            observation.guardrails
            if len(observation.phases) == 1
            else [
                phase.guardrails if phase.status == "PASS" else None for phase in observation.phases
            ]
        ),
        "interruption_call_ids": observation.interruption_call_ids,
        "interruption_count": observation.interruption_count,
        "remaining_model_steps": observation.remaining_model_steps,
        "exception": observation.exception,
        "phases": [_phase_parity_projection(phase) for phase in observation.phases],
    }


def _phase_parity_projection(phase: PhaseObservation) -> dict[str, Any]:
    value = phase.as_json()
    value.pop("stream_event_types")
    if phase.status == "ERROR":
        for result_only_field in (
            "final_output",
            "last_agent",
            "new_items",
            "usage",
            "guardrails",
            "tool_linkage",
        ):
            value.pop(result_only_field)
    return value


def _tool_linkage(
    scenario: Scenario,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]] | None,
) -> InvariantResult:
    del scenario, phase_contracts
    if not observations:
        return _not_run("tool_linkage", "NO_OBSERVATIONS")
    failures: dict[str, Any] = {}
    for variant, observation in observations.items():
        channels: list[tuple[str, list[dict[str, str]], Counter[str]]] = []
        prior_pending: Counter[str] = Counter()
        for phase_index, phase in enumerate(observation.phases):
            pending = Counter(phase.interruption_call_ids)
            linkage = phase.tool_linkage
            generated = cast(list[dict[str, str]], linkage["generated"])
            session_before = cast(list[dict[str, str]] | None, linkage["session_before"])
            session_after = cast(list[dict[str, str]] | None, linkage["session_after"])
            model_inputs = cast(list[list[dict[str, str]]], linkage["model_inputs"])
            prefix = f"phases[{phase_index}]"
            channels.append((f"{prefix}.generated", generated, pending))
            if session_before is not None:
                channels.append((f"{prefix}.session_before", session_before, prior_pending))
            if session_after is not None:
                channels.append((f"{prefix}.session_after", session_after, pending))
            channels.extend(
                (f"{prefix}.model_inputs[{index}]", events, Counter())
                for index, events in enumerate(model_inputs)
            )
            prior_pending = pending
        if len(observation.phases) == 1:
            aggregate = observation.tool_linkage
            aggregate_pending = Counter(observation.interruption_call_ids)
            channels.append(
                (
                    "generated",
                    cast(list[dict[str, str]], aggregate["generated"]),
                    aggregate_pending,
                )
            )
            aggregate_session = cast(list[dict[str, str]] | None, aggregate["session"])
            if aggregate_session is not None:
                channels.append(("session", aggregate_session, aggregate_pending))
            channels.extend(
                (f"model_inputs[{index}]", events, Counter())
                for index, events in enumerate(
                    cast(list[list[dict[str, str]]], aggregate["model_inputs"])
                )
            )
        channel_failures = {
            name: problem
            for name, events, channel_pending in channels
            if (problem := _linkage_problem(events, pending=channel_pending)) is not None
        }
        all_problems = list(channel_failures.values())
        invalid = "INVALID" in pending or any(
            cast(bool, problem["invalid_identifier"]) for problem in all_problems
        )
        duplicate_calls = _problem_identifiers(all_problems, "duplicate_call_ids")
        duplicate_outputs = _problem_identifiers(all_problems, "duplicate_output_ids")
        out_of_order_outputs = _problem_identifiers(all_problems, "out_of_order_output_ids")
        orphan_outputs = _problem_identifiers(all_problems, "orphan_output_ids")
        missing_outputs = _problem_identifiers(all_problems, "missing_output_ids")
        invalid_pending = _problem_identifiers(all_problems, "invalid_pending_ids")
        for phase in observation.phases:
            if phase.interruption_count != len(phase.interruption_call_ids):
                invalid_pending.append(f"{phase.phase_id}:COUNT_MISMATCH")
        if (
            invalid
            or duplicate_calls
            or duplicate_outputs
            or out_of_order_outputs
            or orphan_outputs
            or invalid_pending
            or missing_outputs
        ):
            failures[variant.value] = {
                "invalid_identifier": invalid,
                "duplicate_call_ids": duplicate_calls,
                "duplicate_output_ids": duplicate_outputs,
                "out_of_order_output_ids": out_of_order_outputs,
                "orphan_output_ids": orphan_outputs,
                "missing_output_ids": missing_outputs,
                "invalid_pending_ids": sorted(set(invalid_pending)),
                "channels": channel_failures,
            }
    if failures:
        return _fail("tool_linkage", "INCOHERENT_TOOL_HISTORY", failures)
    return _pass("tool_linkage", {"variants": len(observations)})


def _problem_identifiers(problems: list[dict[str, Any]], key: str) -> list[str]:
    return sorted(
        {identifier for problem in problems for identifier in cast(list[str], problem[key])}
    )


def _linkage_problem(
    events: list[dict[str, str]], *, pending: Counter[str]
) -> dict[str, Any] | None:
    calls = Counter(event["call_id_sha256"] for event in events if event["kind"] == "call")
    outputs = Counter(event["call_id_sha256"] for event in events if event["kind"] == "output")
    invalid = "INVALID" in calls or "INVALID" in outputs or "INVALID" in pending
    duplicate_calls = {key: count for key, count in calls.items() if count > 1}
    duplicate_outputs = {key: count for key, count in outputs.items() if count > 1}
    seen_calls: set[str] = set()
    out_of_order_outputs: set[str] = set()
    for event in events:
        identifier = event["call_id_sha256"]
        if event["kind"] == "call":
            seen_calls.add(identifier)
        elif identifier not in seen_calls:
            out_of_order_outputs.add(identifier)
    orphan_outputs = {
        key: count for key, count in outputs.items() if key == "INVALID" or count > calls[key]
    }
    invalid_pending = {
        key: count
        for key, count in pending.items()
        if key == "INVALID"
        or count != 1
        or (key in calls and (calls[key] != 1 or outputs[key] != 0))
    }
    missing_outputs = {
        key: count - outputs[key]
        for key, count in calls.items()
        if count > outputs[key] and not (count == 1 and outputs[key] == 0 and pending[key] == 1)
    }
    if not (
        invalid
        or duplicate_calls
        or duplicate_outputs
        or out_of_order_outputs
        or orphan_outputs
        or invalid_pending
        or missing_outputs
    ):
        return None
    return {
        "calls": sum(calls.values()),
        "outputs": sum(outputs.values()),
        "invalid_identifier": invalid,
        "duplicate_call_ids": sorted(duplicate_calls),
        "duplicate_output_ids": sorted(duplicate_outputs),
        "out_of_order_output_ids": sorted(out_of_order_outputs),
        "orphan_output_ids": sorted(orphan_outputs),
        "missing_output_ids": sorted(missing_outputs),
        "invalid_pending_ids": sorted(invalid_pending),
    }


def _exactly_once(
    scenario: Scenario,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]] | None,
) -> InvariantResult:
    del phase_contracts
    if not scenario.expected_tool_counts:
        return _not_run("exactly_once", "NO_EXPECTED_COUNTS")
    failures: dict[str, Any] = {}
    expected = dict(sorted(scenario.expected_tool_counts.items()))
    for variant, observation in observations.items():
        if observation.tool_counts != expected:
            failures[variant.value] = {
                "expected": expected,
                "actual": observation.tool_counts,
            }
    if failures:
        return _fail("exactly_once", "SIDE_EFFECT_COUNT_MISMATCH", failures)
    return _pass("exactly_once", {"expected": expected})


def _model_script_consumed(
    scenario: Scenario,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]] | None,
) -> InvariantResult:
    del scenario, phase_contracts
    remaining: dict[str, int] = {}
    for variant, observation in observations.items():
        final_by_model: dict[str, PhaseObservation] = {}
        for phase in observation.phases:
            final_by_model[phase.model_group] = phase
        for model_group, phase in final_by_model.items():
            if phase.remaining_model_steps != 0:
                remaining[f"{variant.value}:{model_group}"] = phase.remaining_model_steps
    if remaining:
        return _fail("model_script_consumed", "UNCONSUMED_MODEL_STEPS", remaining)
    return _pass("model_script_consumed", {"variants": len(observations)})


def _state_transitions(
    scenario: Scenario,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]] | None,
) -> InvariantResult:
    del scenario
    if phase_contracts is None:
        return _not_run("state_transitions", "NO_PHASE_CONTRACTS")
    failures: dict[str, Any] = {}
    for variant, observation in observations.items():
        contracts = phase_contracts.get(variant)
        if contracts is None or len(contracts) != len(observation.phases):
            failures[variant.value] = {"reason": "PHASE_CONTRACT_MISMATCH"}
            continue
        variant_failures: dict[str, Any] = {}
        phases_by_id = {phase.phase_id: phase for phase in observation.phases}
        for phase, contract in zip(observation.phases, contracts, strict=True):
            transition = phase.state_transition
            problems: list[str] = []
            if transition.get("kind") != contract.input_kind:
                problems.append("INPUT_KIND_MISMATCH")
            if transition.get("source_phase") != contract.source_phase:
                problems.append("SOURCE_PHASE_MISMATCH")
            if transition.get("json_round_trip_requested") != contract.json_round_trip:
                problems.append("ROUND_TRIP_CONTRACT_MISMATCH")
            if contract.input_kind == "literal":
                if (
                    transition.get("json_round_trip_equal") is not None
                    or transition.get("restored_state_equal") is not None
                    or transition.get("state_schema_version") is not None
                    or transition.get("source_interruption_call_ids") != []
                    or transition.get("restored_interruption_call_ids") != []
                    or transition.get("decisions") != []
                ):
                    problems.append("UNEXPECTED_LITERAL_TRANSITION_STATE")
            else:
                if contract.json_round_trip and transition.get("json_round_trip_equal") is not True:
                    problems.append("RUNSTATE_JSON_ROUND_TRIP_CHANGED")
                if contract.json_round_trip and transition.get("restored_state_equal") is not True:
                    problems.append("RUNSTATE_RESTORED_STATE_CHANGED")
                if contract.json_round_trip and not transition.get("state_schema_version"):
                    problems.append("RUNSTATE_SCHEMA_VERSION_MISSING")
                if not contract.json_round_trip and (
                    transition.get("json_round_trip_equal") is not None
                    or transition.get("restored_state_equal") is not None
                    or transition.get("state_schema_version") is not None
                ):
                    problems.append("UNEXPECTED_RUNSTATE_ROUND_TRIP_STATE")
                source_ids = transition.get("source_interruption_call_ids")
                restored_ids = transition.get("restored_interruption_call_ids")
                source_phase = phases_by_id.get(contract.source_phase or "")
                if source_phase is None:
                    problems.append("SOURCE_PHASE_OBSERVATION_MISSING")
                elif (
                    source_ids != source_phase.interruption_call_ids
                    or source_phase.interruption_count != len(source_phase.interruption_call_ids)
                ):
                    problems.append("SOURCE_INTERRUPTION_IDS_CHANGED")
                if source_ids != restored_ids:
                    problems.append("RESTORED_INTERRUPTION_IDS_CHANGED")
                actual_decisions = transition.get("decisions")
                expected_decisions = [
                    {
                        "action": decision["action"],
                        "call_id_sha256": decision["call_id_sha256"],
                    }
                    for decision in contract.decisions
                ]
                projected_actual = (
                    [
                        {
                            "action": decision.get("action"),
                            "call_id_sha256": decision.get("call_id_sha256"),
                        }
                        for decision in actual_decisions
                        if isinstance(decision, dict)
                    ]
                    if isinstance(actual_decisions, list)
                    else None
                )
                if projected_actual != expected_decisions:
                    problems.append("DECISION_CONTRACT_MISMATCH")
                if not isinstance(actual_decisions, list) or any(
                    not isinstance(decision, dict) or decision.get("matched") is not True
                    for decision in actual_decisions
                ):
                    problems.append("DECISION_NOT_EXACTLY_MATCHED")
                if isinstance(restored_ids, list) and any(
                    decision["call_id_sha256"] not in restored_ids
                    for decision in expected_decisions
                ):
                    problems.append("DECISION_TARGET_NOT_INTERRUPTED")
            if problems:
                variant_failures[phase.phase_id] = problems
        if variant_failures:
            failures[variant.value] = variant_failures
    if failures:
        return _fail("state_transitions", "INVALID_STATE_TRANSITION", failures)
    return _pass(
        "state_transitions",
        {"variants": len(observations), "round_trips": _round_trip_count(phase_contracts)},
    )


def _round_trip_count(
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]],
) -> int:
    return sum(
        1
        for contracts in phase_contracts.values()
        for contract in contracts
        if contract.json_round_trip
    )


def _state_fork_isolation(
    scenario: Scenario,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]] | None,
) -> InvariantResult:
    del scenario
    if phase_contracts is None:
        return _not_run("state_fork_isolation", "NO_PHASE_CONTRACTS")
    invalid: dict[str, Any] = {}
    mutated: dict[str, Any] = {}
    fork_count = 0
    for variant, observation in observations.items():
        contracts = phase_contracts.get(variant)
        if contracts is None or len(contracts) != len(observation.phases):
            invalid[variant.value] = {"reason": "PHASE_CONTRACT_MISMATCH"}
            continue
        variant_invalid: dict[str, Any] = {}
        variant_mutated: dict[str, Any] = {}
        for phase, contract in zip(observation.phases, contracts, strict=True):
            transition = phase.state_transition
            expected_decisions = [
                {
                    "action": decision["action"],
                    "call_id_sha256": decision["call_id_sha256"],
                }
                for decision in contract.sibling_decisions
            ]
            if not expected_decisions:
                if _unexpected_state_fork_data(transition):
                    variant_invalid[phase.phase_id] = ["UNEXPECTED_STATE_FORK_DATA"]
                continue
            fork_count += 1
            problems: list[str] = []
            if (
                contract.input_kind != "resume"
                or contract.json_round_trip
                or bool(contract.decisions)
            ):
                problems.append("INVALID_STATE_FORK_CONTRACT")
            sibling_ids = transition.get("sibling_interruption_call_ids")
            source_ids = transition.get("source_interruption_call_ids")
            if not isinstance(sibling_ids, list) or sibling_ids != source_ids:
                problems.append("SIBLING_INTERRUPTION_IDS_CHANGED")
            actual_decisions = transition.get("sibling_decisions")
            projected_actual = (
                [
                    {
                        "action": decision.get("action"),
                        "call_id_sha256": decision.get("call_id_sha256"),
                    }
                    for decision in actual_decisions
                    if isinstance(decision, dict)
                ]
                if isinstance(actual_decisions, list)
                else None
            )
            if projected_actual != expected_decisions:
                problems.append("SIBLING_DECISION_CONTRACT_MISMATCH")
            if not isinstance(actual_decisions, list) or any(
                not isinstance(decision, dict) or decision.get("matched") is not True
                for decision in actual_decisions
            ):
                problems.append("SIBLING_DECISION_NOT_EXACTLY_MATCHED")
            if isinstance(sibling_ids, list) and any(
                decision["call_id_sha256"] not in sibling_ids for decision in expected_decisions
            ):
                problems.append("SIBLING_DECISION_TARGET_NOT_INTERRUPTED")
            before = transition.get("subject_state_before_sha256")
            after = transition.get("subject_state_after_sha256")
            unchanged = transition.get("subject_state_unchanged")
            if not _sha256_digest(before) or not _sha256_digest(after):
                problems.append("SUBJECT_STATE_DIGEST_MISSING")
            elif not isinstance(unchanged, bool) or unchanged != (before == after):
                problems.append("SUBJECT_STATE_DIGEST_MISMATCH")
            if problems:
                variant_invalid[phase.phase_id] = problems
            elif unchanged is not True:
                variant_mutated[phase.phase_id] = {
                    "subject_state_before_sha256": before,
                    "subject_state_after_sha256": after,
                }
        if variant_invalid:
            invalid[variant.value] = variant_invalid
        if variant_mutated:
            mutated[variant.value] = variant_mutated
    if invalid:
        return _fail(
            "state_fork_isolation",
            "INVALID_STATE_FORK_TRANSITION",
            invalid,
        )
    if fork_count == 0:
        return _not_run("state_fork_isolation", "NO_STATE_FORKS")
    if mutated:
        return _fail(
            "state_fork_isolation",
            "SIBLING_STATE_MUTATED",
            mutated,
        )
    return _pass(
        "state_fork_isolation",
        {"variants": len(observations), "forks": fork_count},
    )


def _unexpected_state_fork_data(transition: dict[str, JsonValue]) -> bool:
    return (
        transition.get("subject_state_before_sha256") is not None
        or transition.get("subject_state_after_sha256") is not None
        or transition.get("subject_state_unchanged") is not None
        or transition.get("sibling_interruption_call_ids", []) != []
        or transition.get("sibling_decisions", []) != []
    )


def _sha256_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _phase_contract(
    scenario: Scenario,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]] | None,
) -> InvariantResult:
    del scenario
    if phase_contracts is None:
        return _not_run("phase_contract", "NO_PHASE_CONTRACTS")
    failures: dict[str, Any] = {}
    for variant, observation in observations.items():
        contracts = phase_contracts.get(variant)
        if contracts is None or len(contracts) != len(observation.phases):
            failures[variant.value] = {"reason": "PHASE_CONTRACT_MISMATCH"}
            continue
        variant_failures: dict[str, Any] = {}
        for phase, contract in zip(observation.phases, contracts, strict=True):
            mismatches: dict[str, Any] = {}
            if phase.phase_id != contract.phase_id:
                mismatches["phase_id"] = {
                    "expected": contract.phase_id,
                    "actual": phase.phase_id,
                }
            if phase.input_kind != contract.input_kind:
                mismatches["input_kind"] = {
                    "expected": contract.input_kind,
                    "actual": phase.input_kind,
                }
            if phase.model_group != contract.model_group:
                mismatches["model_group"] = {
                    "expected": contract.model_group,
                    "actual": phase.model_group,
                }
            expected_delta = dict(contract.expected_tool_counts_delta)
            if phase.tool_counts_delta != expected_delta:
                mismatches["tool_counts_delta"] = {
                    "expected": expected_delta,
                    "actual": phase.tool_counts_delta,
                }
            expected_probes = dict(contract.expected_probes_after)
            if phase.probes_after != expected_probes:
                mismatches["probes_after"] = {
                    "expected": expected_probes,
                    "actual": phase.probes_after,
                }
            if set(phase.probes_before) != set(expected_probes):
                mismatches["probes_before_names"] = {
                    "expected": sorted(expected_probes),
                    "actual": sorted(phase.probes_before),
                }
            if mismatches:
                variant_failures[phase.phase_id] = mismatches
        if variant_failures:
            failures[variant.value] = variant_failures
    if failures:
        return _fail("phase_contract", "PHASE_OBSERVATION_MISMATCH", failures)
    return _pass(
        "phase_contract",
        {
            "variants": len(observations),
            "phases": sum(len(v.phases) for v in observations.values()),
        },
    )


def _session_replay(
    scenario: Scenario,
    observations: dict[RunVariant, Observation],
    phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]] | None,
) -> InvariantResult:
    del scenario, phase_contracts
    failures: dict[str, Any] = {}
    comparisons = 0
    for variant, observation in observations.items():
        variant_failures: dict[str, Any] = {}
        for phase in observation.phases:
            linkage = phase.tool_linkage
            session_before = cast(list[dict[str, str]] | None, linkage.get("session_before"))
            model_inputs = cast(list[list[dict[str, str]]], linkage.get("model_inputs"))
            if not session_before or not model_inputs:
                continue
            comparisons += 1
            if not _is_subsequence(session_before, model_inputs[0]):
                variant_failures[phase.phase_id] = {
                    "session_tool_events": session_before,
                    "model_input_tool_events": model_inputs[0],
                }
        if variant_failures:
            failures[variant.value] = variant_failures
    if failures:
        return _fail("session_replay", "SESSION_TOOL_HISTORY_NOT_REPLAYED", failures)
    if comparisons == 0:
        return _not_run("session_replay", "NO_PERSISTED_TOOL_HISTORY_TO_COMPARE")
    return _pass("session_replay", {"comparisons": comparisons})


def _is_subsequence(left: list[Any], right: list[Any]) -> bool:
    if not left:
        return True
    index = 0
    for item in right:
        if item == left[index]:
            index += 1
            if index == len(left):
                return True
    return False


def _pass(name: str, details: Any) -> InvariantResult:
    return InvariantResult(name=name, status="PASS", reason="OK", details=to_json_value(details))


def _fail(name: str, reason: str, details: Any) -> InvariantResult:
    return InvariantResult(
        name=name,
        status="FAIL",
        reason=reason,
        details=to_json_value(details),
    )


def _not_run(name: str, reason: str) -> InvariantResult:
    return InvariantResult(name=name, status="NOT_RUN", reason=reason, details={})
