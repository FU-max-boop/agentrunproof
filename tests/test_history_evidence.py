from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

import pytest

from agentrunproof._canonical import sha256_hex
from agentrunproof.history.evidence import (
    HistoryEvidenceError,
    finalize_history_matrix,
    history_matrix_json,
    load_history_matrix,
    parse_history_worker_json,
    validate_history_matrix,
)

_TOOL_VERSION = "0.1.0"
_WHEEL_SHA = "a" * 64

_SESSION_MESSAGE = {
    "content": [{"annotations": [], "text": "It is sunny.", "type": "output_text"}],
    "role": "assistant",
    "type": "message",
}
_SESSION_USER = {"content": "What about tomorrow?", "role": "user"}
_SESSION_OUTPUT = {
    "call_id": "history-call-1",
    "output": "sunny",
    "type": "function_call_output",
}

_GUARD_USER = {"content": "Use approval_tool", "role": "user"}
_GUARD_CALL = {
    "arguments": "{}",
    "call_id": "call-approved",
    "id": "agentrunproof-tool-call",
    "name": "approval_tool",
    "type": "function_call",
}
_GUARD_OUTPUT = {
    "call_id": "call-approved",
    "output": "approved-result",
    "type": "function_call_output",
}
_GUARD_CONTINUE = {"content": "Continue", "role": "user"}
_GUARD_MESSAGE = {
    "content": [
        {
            "annotations": [],
            "logprobs": [],
            "text": "done",
            "type": "output_text",
        }
    ],
    "id": "agentrunproof-followup-message",
    "role": "assistant",
    "status": "completed",
    "type": "message",
}
_GUARD_PAIR = [
    {"call_id": "call-approved", "type": "function_call"},
    {
        "call_id": "call-approved",
        "output": "approved-result",
        "type": "function_call_output",
    },
]


def _session_variant(*, fixed: bool) -> dict[str, Any]:
    model_input = (
        [_SESSION_MESSAGE, _SESSION_USER]
        if fixed
        else [_SESSION_OUTPUT, _SESSION_MESSAGE, _SESSION_USER]
    )
    return {
        "exception_type": None,
        "model_input": copy.deepcopy(model_input),
        "observed_types": (
            ["message", "user"] if fixed else ["function_call_output", "message", "user"]
        ),
        "orphan_call_ids": [] if fixed else ["history-call-1"],
        "reason": "OK" if fixed else "ORPHAN_FUNCTION_OUTPUT_REACHED_MODEL",
        "status": "PASS" if fixed else "FAIL",
    }


def _approval_variant(*, fixed: bool) -> dict[str, Any]:
    common = {
        "approved_call_id": "approval-call-1",
        "exception_type": None,
        "initial_interruption_call_ids": ["approval-call-1"],
        "initial_model_call_count": 1,
        "restored_interruption_call_ids": ["approval-call-1"],
        "state_json_round_trip": True,
    }
    if not fixed:
        return {
            **common,
            "final_output": None,
            "reason": "APPROVAL_NOT_HONORED_WITH_CONTEXT_OVERRIDE",
            "resumed_interruption_call_ids": ["approval-call-1"],
            "resumed_model_call_count": 0,
            "resumed_model_remaining_steps": 1,
            "state_schema_version": "1.13",
            "status": "FAIL",
            "tool_invocation_count": 0,
            "tool_invocations": [],
        }
    return {
        **common,
        "final_output": "done",
        "reason": "OK",
        "resumed_interruption_call_ids": [],
        "resumed_model_call_count": 1,
        "resumed_model_remaining_steps": 0,
        "state_schema_version": "1.15",
        "status": "PASS",
        "tool_invocation_count": 1,
        "tool_invocations": [{"arguments": {}, "call_id": "approval-call-1", "principal": "ella"}],
    }


def _guardrail_variant(*, broken_streaming: bool) -> dict[str, Any]:
    if broken_streaming:
        session_after_tripwire = [_GUARD_USER, _GUARD_CALL]
        followup_model_input = [_GUARD_USER, _GUARD_CONTINUE]
        session_after_followup = [
            _GUARD_USER,
            _GUARD_CALL,
            _GUARD_CONTINUE,
            _GUARD_MESSAGE,
        ]
        durable_sequence = [{"call_id": "call-approved", "type": "function_call"}]
        replayed_sequence: list[dict[str, Any]] = []
        violations = [
            "COMMITTED_TOOL_PAIR_NOT_DURABLE",
            "FOLLOWUP_MODEL_INPUT_MISSING_TOOL_PAIR",
        ]
    else:
        session_after_tripwire = [_GUARD_USER, _GUARD_CALL, _GUARD_OUTPUT]
        followup_model_input = [
            _GUARD_USER,
            _GUARD_CALL,
            _GUARD_OUTPUT,
            _GUARD_CONTINUE,
        ]
        session_after_followup = [
            _GUARD_USER,
            _GUARD_CALL,
            _GUARD_OUTPUT,
            _GUARD_CONTINUE,
            _GUARD_MESSAGE,
        ]
        durable_sequence = _GUARD_PAIR
        replayed_sequence = _GUARD_PAIR
        violations = []
    return {
        "durable_tool_sequence": copy.deepcopy(durable_sequence),
        "first_exception_type": None,
        "followup_exception_type": None,
        "followup_final_output": "done",
        "followup_model_input": copy.deepcopy(followup_model_input),
        "interruption_count": 1,
        "reason": violations[0] if violations else "OK",
        "replayed_tool_sequence": copy.deepcopy(replayed_sequence),
        "resume_exception_type": "OutputGuardrailTripwireTriggered",
        "session_after_followup": copy.deepcopy(session_after_followup),
        "session_after_tripwire": copy.deepcopy(session_after_tripwire),
        "side_effect_count": 1,
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
    }


def _result(case_id: str, sdk_version: str) -> dict[str, Any]:
    if case_id == "session-limit-orphan-output":
        fixed = sdk_version == "0.20.0"
        variant = _session_variant(fixed=fixed)
        result_case_id = "openai-agents-python-4322-session-limit-orphan-output"
        overall = "PASS" if fixed else "FAIL"
        variants = {"non_streaming": variant, "streaming": copy.deepcopy(variant)}
    elif case_id == "runstate-context-approval":
        fixed = sdk_version == "0.20.0"
        variant = _approval_variant(fixed=fixed)
        result_case_id = "openai-agents-python-4244-runstate-context-approval"
        overall = "PASS" if fixed else "FAIL"
        variants = {"non_streaming": variant, "streaming": copy.deepcopy(variant)}
    else:
        fixed = sdk_version == "0.19.3"
        result_case_id = "openai-agents-python-4125-resumed-guardrail-atomicity"
        overall = "PASS" if fixed else "FAIL"
        variants = {
            "non_streaming": _guardrail_variant(broken_streaming=False),
            "streaming": _guardrail_variant(broken_streaming=not fixed),
        }
    return {
        "case_id": result_case_id,
        "case_revision": 1,
        "overall_status": overall,
        "public_api_only": True,
        "variants": variants,
    }


def _run(case_id: str, sdk_version: str, expected: str) -> dict[str, Any]:
    result = _result(case_id, sdk_version)
    return {
        "case_id": case_id,
        "expected": expected,
        "observed": result["overall_status"],
        "sdk_version": sdk_version,
        "worker": {
            "agentrunproof_version": _TOOL_VERSION,
            "openai_agents_version": sdk_version,
            "python": "3.12.13",
            "result": result,
            "schema_version": "agentrunproof.history-observation/v1",
        },
        "worker_exit": 0 if expected == "PASS" else 1,
    }


def _draft_matrix() -> dict[str, Any]:
    return {
        "expectations_matched": True,
        "runs": [
            _run("session-limit-orphan-output", "0.19.4", "FAIL"),
            _run("session-limit-orphan-output", "0.20.0", "PASS"),
            _run("runstate-context-approval", "0.19.4", "FAIL"),
            _run("runstate-context-approval", "0.20.0", "PASS"),
            _run("resumed-guardrail-atomicity", "0.19.2", "FAIL"),
            _run("resumed-guardrail-atomicity", "0.19.3", "PASS"),
        ],
        "schema_version": "agentrunproof.history-matrix/v1",
        "wheel": {
            "name": f"agentrunproof-{_TOOL_VERSION}-py3-none-any.whl",
            "sha256": _WHEEL_SHA,
        },
    }


def _valid_matrix() -> dict[str, Any]:
    return finalize_history_matrix(_draft_matrix())


def _readdress(matrix: dict[str, Any]) -> None:
    matrix["matrix_id"] = None
    matrix["matrix_id"] = f"sha256:{sha256_hex(matrix)}"


def test_valid_six_run_matrix_is_content_addressed_and_round_trips(tmp_path: Path) -> None:
    first = _valid_matrix()
    second = finalize_history_matrix(_draft_matrix())

    assert first == second
    assert first["matrix_id"].startswith("sha256:")
    assert validate_history_matrix(first) == first

    path = tmp_path / "matrix.json"
    path.write_text(history_matrix_json(first), encoding="utf-8")
    assert load_history_matrix(path) == first


def test_raw_tamper_is_rejected_even_after_attacker_recomputes_matrix_id() -> None:
    forged = _valid_matrix()
    variant = forged["runs"][1]["worker"]["result"]["variants"]["streaming"]
    variant["model_input"][0]["content"][0]["text"] = "forged"
    _readdress(forged)

    with pytest.raises(HistoryEvidenceError, match="fingerprint"):
        validate_history_matrix(forged)


def test_label_swap_is_rejected_even_when_worker_metadata_is_swapped_too() -> None:
    forged = _valid_matrix()
    forged["runs"][0], forged["runs"][1] = forged["runs"][1], forged["runs"][0]
    _readdress(forged)

    with pytest.raises(HistoryEvidenceError, match=r"runs\[0\] must be"):
        validate_history_matrix(forged)


def test_arbitrary_exception_cannot_impersonate_the_expected_buggy_failure() -> None:
    forged = _valid_matrix()
    variant = forged["runs"][2]["worker"]["result"]["variants"]["non_streaming"]
    variant["exception_type"] = "RuntimeError"
    variant["reason"] = "SCENARIO_EXCEPTION"
    variant["status"] = "FAIL"
    _readdress(forged)

    with pytest.raises(HistoryEvidenceError, match="exception_type fingerprint"):
        validate_history_matrix(forged)


def test_4125_buggy_release_must_have_pass_fail_asymmetry_not_two_failures() -> None:
    forged = _valid_matrix()
    variants = forged["runs"][4]["worker"]["result"]["variants"]
    variants["non_streaming"] = copy.deepcopy(variants["streaming"])
    _readdress(forged)

    with pytest.raises(HistoryEvidenceError, match="fingerprint"):
        validate_history_matrix(forged)


def test_summary_labels_cannot_override_recomputed_raw_semantics() -> None:
    forged = _valid_matrix()
    variant = forged["runs"][0]["worker"]["result"]["variants"]["streaming"]
    variant["status"] = "PASS"
    variant["reason"] = "OK"
    forged["runs"][0]["worker"]["result"]["overall_status"] = "PASS"
    forged["runs"][0]["observed"] = "PASS"
    forged["runs"][0]["worker_exit"] = 0
    _readdress(forged)

    with pytest.raises(HistoryEvidenceError, match="status contradicts"):
        validate_history_matrix(forged)


def test_duplicate_run_identity_is_rejected() -> None:
    forged = _valid_matrix()
    forged["runs"][1] = copy.deepcopy(forged["runs"][0])
    _readdress(forged)

    with pytest.raises(HistoryEvidenceError, match="Duplicate history run identity"):
        validate_history_matrix(forged)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update({"extra": True}), "unexpected fields"),
        (lambda value: value["wheel"].pop("sha256"), "unexpected fields"),
        (
            lambda value: value["runs"][0]["worker"]["result"]["variants"].update({"extra": {}}),
            "unexpected fields",
        ),
    ],
)
def test_extra_and_missing_fields_are_rejected(
    mutation: Any,
    message: str,
) -> None:
    draft = _draft_matrix()
    mutation(draft)

    with pytest.raises(HistoryEvidenceError, match=message):
        finalize_history_matrix(draft)


def test_non_finite_values_are_rejected_before_addressing() -> None:
    draft = _draft_matrix()
    draft["wheel"]["sha256"] = math.nan

    with pytest.raises(HistoryEvidenceError, match="not canonical JSON"):
        finalize_history_matrix(draft)


def test_non_string_mapping_keys_fail_closed() -> None:
    draft = _draft_matrix()
    draft[1] = "not-json"

    with pytest.raises(HistoryEvidenceError, match="string field names"):
        finalize_history_matrix(draft)


def test_loader_rejects_duplicate_json_object_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"first","schema_version":"second"}', encoding="utf-8")

    with pytest.raises(HistoryEvidenceError, match="Duplicate JSON object key"):
        load_history_matrix(path)


def test_loader_rejects_non_finite_json_numbers(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{"value":NaN}', encoding="utf-8")

    with pytest.raises(HistoryEvidenceError, match="Non-finite JSON number"):
        load_history_matrix(path)


def test_worker_parser_rejects_duplicate_keys_and_non_finite_numbers() -> None:
    with pytest.raises(HistoryEvidenceError, match="Duplicate JSON object key"):
        parse_history_worker_json('{"result":{},"result":{}}')
    with pytest.raises(HistoryEvidenceError, match="Non-finite JSON number"):
        parse_history_worker_json('{"value":Infinity}')


def test_finalize_requires_unaddressed_input() -> None:
    addressed = _valid_matrix()

    with pytest.raises(HistoryEvidenceError, match="null matrix_id"):
        finalize_history_matrix(addressed)
