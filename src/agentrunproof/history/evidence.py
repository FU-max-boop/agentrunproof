from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, cast

from .._canonical import CanonicalizationError, JsonValue, sha256_hex

MATRIX_SCHEMA_VERSION: Final = "agentrunproof.history-matrix/v1"
WORKER_SCHEMA_VERSION: Final = "agentrunproof.history-observation/v1"

_MATRIX_ID_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_PYTHON_VERSION_PATTERN: Final = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_WHEEL_PATTERN: Final = re.compile(
    r"^agentrunproof-(?P<version>[A-Za-z0-9][A-Za-z0-9_.!+]*)-py3-none-any\.whl$"
)

_VARIANTS: Final = ("non_streaming", "streaming")
_MATRIX_KEYS: Final = {
    "schema_version",
    "matrix_id",
    "wheel",
    "runs",
    "expectations_matched",
}
_RUN_KEYS: Final = {
    "case_id",
    "sdk_version",
    "expected",
    "observed",
    "worker_exit",
    "worker",
}
_WORKER_KEYS: Final = {
    "schema_version",
    "agentrunproof_version",
    "openai_agents_version",
    "python",
    "result",
}
_RESULT_KEYS: Final = {
    "case_id",
    "case_revision",
    "overall_status",
    "public_api_only",
    "variants",
}

_SESSION_VARIANT_KEYS: Final = {
    "status",
    "reason",
    "model_input",
    "observed_types",
    "orphan_call_ids",
    "exception_type",
}
_APPROVAL_VARIANT_KEYS: Final = {
    "status",
    "reason",
    "initial_interruption_call_ids",
    "restored_interruption_call_ids",
    "resumed_interruption_call_ids",
    "approved_call_id",
    "tool_invocations",
    "tool_invocation_count",
    "final_output",
    "state_json_round_trip",
    "state_schema_version",
    "initial_model_call_count",
    "resumed_model_call_count",
    "resumed_model_remaining_steps",
    "exception_type",
}
_GUARDRAIL_VARIANT_KEYS: Final = {
    "status",
    "reason",
    "violations",
    "interruption_count",
    "resume_exception_type",
    "side_effect_count",
    "session_after_tripwire",
    "session_after_followup",
    "durable_tool_sequence",
    "followup_model_input",
    "replayed_tool_sequence",
    "followup_final_output",
    "first_exception_type",
    "followup_exception_type",
}

_SESSION_MESSAGE: Final[dict[str, JsonValue]] = {
    "content": [
        {
            "annotations": [],
            "text": "It is sunny.",
            "type": "output_text",
        }
    ],
    "role": "assistant",
    "type": "message",
}
_SESSION_USER: Final[dict[str, JsonValue]] = {
    "content": "What about tomorrow?",
    "role": "user",
}
_SESSION_ORPHAN_OUTPUT: Final[dict[str, JsonValue]] = {
    "call_id": "history-call-1",
    "output": "sunny",
    "type": "function_call_output",
}

_APPROVAL_CALL_ID: Final = "approval-call-1"
_APPROVAL_INVOCATION: Final[dict[str, JsonValue]] = {
    "arguments": {},
    "call_id": _APPROVAL_CALL_ID,
    "principal": "ella",
}

_GUARDRAIL_CALL_ID: Final = "call-approved"
_GUARDRAIL_USER: Final[dict[str, JsonValue]] = {
    "content": "Use approval_tool",
    "role": "user",
}
_GUARDRAIL_CALL: Final[dict[str, JsonValue]] = {
    "arguments": "{}",
    "call_id": _GUARDRAIL_CALL_ID,
    "id": "agentrunproof-tool-call",
    "name": "approval_tool",
    "type": "function_call",
}
_GUARDRAIL_OUTPUT: Final[dict[str, JsonValue]] = {
    "call_id": _GUARDRAIL_CALL_ID,
    "output": "approved-result",
    "type": "function_call_output",
}
_GUARDRAIL_CONTINUE: Final[dict[str, JsonValue]] = {
    "content": "Continue",
    "role": "user",
}
_GUARDRAIL_MESSAGE: Final[dict[str, JsonValue]] = {
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
_GUARDRAIL_TOOL_PAIR: Final[list[JsonValue]] = [
    {"call_id": _GUARDRAIL_CALL_ID, "type": "function_call"},
    {
        "call_id": _GUARDRAIL_CALL_ID,
        "output": "approved-result",
        "type": "function_call_output",
    },
]


class HistoryEvidenceError(ValueError):
    """Raised when historical matrix evidence is malformed or semantically false."""


class _RunSpec:
    __slots__ = ("case_id", "sdk_version", "expected", "result_case_id", "fingerprint")

    def __init__(
        self,
        case_id: str,
        sdk_version: str,
        expected: str,
        result_case_id: str,
        fingerprint: str,
    ) -> None:
        self.case_id = case_id
        self.sdk_version = sdk_version
        self.expected = expected
        self.result_case_id = result_case_id
        self.fingerprint = fingerprint


_RUN_SPECS: Final = (
    _RunSpec(
        "session-limit-orphan-output",
        "0.19.4",
        "FAIL",
        "openai-agents-python-4322-session-limit-orphan-output",
        "buggy",
    ),
    _RunSpec(
        "session-limit-orphan-output",
        "0.20.0",
        "PASS",
        "openai-agents-python-4322-session-limit-orphan-output",
        "fixed",
    ),
    _RunSpec(
        "runstate-context-approval",
        "0.19.4",
        "FAIL",
        "openai-agents-python-4244-runstate-context-approval",
        "buggy",
    ),
    _RunSpec(
        "runstate-context-approval",
        "0.20.0",
        "PASS",
        "openai-agents-python-4244-runstate-context-approval",
        "fixed",
    ),
    _RunSpec(
        "resumed-guardrail-atomicity",
        "0.19.2",
        "FAIL",
        "openai-agents-python-4125-resumed-guardrail-atomicity",
        "buggy",
    ),
    _RunSpec(
        "resumed-guardrail-atomicity",
        "0.19.3",
        "PASS",
        "openai-agents-python-4125-resumed-guardrail-atomicity",
        "fixed",
    ),
)


def finalize_history_matrix(matrix: Any) -> dict[str, JsonValue]:
    """Add a content address to an unaddressed matrix, then validate it.

    The accepted draft shape is the strict matrix v1 shape with ``matrix_id`` either
    absent or null. This function does not execute workers or attest to their environment.
    """

    if not isinstance(matrix, dict):
        raise HistoryEvidenceError("The history matrix must be a JSON object.")
    candidate = copy.deepcopy(matrix)
    if "matrix_id" not in candidate:
        candidate["matrix_id"] = None
    elif candidate["matrix_id"] is not None:
        raise HistoryEvidenceError("A matrix being finalized must have a null matrix_id.")
    _require_exact_keys(candidate, _MATRIX_KEYS, "history matrix")
    candidate["matrix_id"] = _matrix_id(candidate)
    return validate_history_matrix(candidate)


def validate_history_matrix(matrix: Any) -> dict[str, JsonValue]:
    """Validate the complete six-run Gate 0 matrix from raw observations.

    Summary labels are checked for consistency, but never used to decide whether a run
    passes. The decision is recomputed from raw Runner/session observations and matched
    against the frozen buggy/fixed fingerprint for that exact SDK release.
    """

    if not isinstance(matrix, dict):
        raise HistoryEvidenceError("The history matrix must be a JSON object.")
    _require_exact_keys(matrix, _MATRIX_KEYS, "history matrix")
    if matrix.get("schema_version") != MATRIX_SCHEMA_VERSION:
        raise HistoryEvidenceError("Unsupported history matrix schema version.")

    matrix_id = matrix.get("matrix_id")
    if not isinstance(matrix_id, str) or not _MATRIX_ID_PATTERN.fullmatch(matrix_id):
        raise HistoryEvidenceError("matrix_id must be a lowercase SHA-256 identifier.")
    if matrix_id != _matrix_id(matrix):
        raise HistoryEvidenceError("matrix_id does not match the canonical matrix payload.")

    wheel = _require_object(matrix.get("wheel"), "wheel")
    _require_exact_keys(wheel, {"name", "sha256"}, "wheel")
    wheel_name = _require_nonempty_string(wheel.get("name"), "wheel.name")
    wheel_match = _WHEEL_PATTERN.fullmatch(wheel_name)
    if wheel_match is None:
        raise HistoryEvidenceError("wheel.name is not the canonical AgentRunProof wheel name.")
    wheel_digest = wheel.get("sha256")
    if not isinstance(wheel_digest, str) or not _SHA256_PATTERN.fullmatch(wheel_digest):
        raise HistoryEvidenceError("wheel.sha256 must be a lowercase SHA-256 digest.")
    tool_version = wheel_match.group("version")

    runs = matrix.get("runs")
    if not isinstance(runs, list):
        raise HistoryEvidenceError("runs must be a JSON array.")
    if len(runs) != len(_RUN_SPECS):
        raise HistoryEvidenceError(
            f"runs must contain exactly {len(_RUN_SPECS)} historical observations."
        )

    seen: set[tuple[str, str]] = set()
    python_version: str | None = None
    recomputed_statuses: list[str] = []
    for index, (raw_run, spec) in enumerate(zip(runs, _RUN_SPECS, strict=True)):
        run = _require_object(raw_run, f"runs[{index}]")
        identity = _run_identity(run, index)
        if identity in seen:
            raise HistoryEvidenceError(f"Duplicate history run identity: {identity!r}.")
        seen.add(identity)
        expected_identity = (spec.case_id, spec.sdk_version)
        if identity != expected_identity:
            raise HistoryEvidenceError(
                f"runs[{index}] must be {expected_identity!r}, got {identity!r}."
            )
        status, observed_python = _validate_run(run, spec, tool_version, index)
        if python_version is None:
            python_version = observed_python
        elif observed_python != python_version:
            raise HistoryEvidenceError("All history workers must use the same Python version.")
        recomputed_statuses.append(status)

    expected_statuses = [spec.expected for spec in _RUN_SPECS]
    if recomputed_statuses != expected_statuses:
        raise HistoryEvidenceError("Raw history outcomes do not match the frozen six-run matrix.")
    if matrix.get("expectations_matched") is not True:
        raise HistoryEvidenceError(
            "expectations_matched must be true after independent semantic recomputation."
        )
    return cast(dict[str, JsonValue], copy.deepcopy(matrix))


def load_history_matrix(path: Path) -> dict[str, JsonValue]:
    """Load strict JSON without duplicate keys or non-finite numbers, then validate it."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise HistoryEvidenceError(f"Cannot read history matrix: {error}") from error
    return parse_history_matrix_json(text)


def parse_history_matrix_json(text: str) -> dict[str, JsonValue]:
    """Parse and validate strict matrix JSON from an already stable byte snapshot."""

    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_finite_json,
        )
    except HistoryEvidenceError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise HistoryEvidenceError(f"Cannot parse history matrix: {error}") from error
    return validate_history_matrix(payload)


def parse_history_worker_json(text: str) -> dict[str, JsonValue]:
    """Parse one worker response without accepting duplicate keys or non-finite values."""

    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_finite_json,
        )
    except HistoryEvidenceError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise HistoryEvidenceError(f"Cannot parse history worker JSON: {error}") from error
    if not isinstance(payload, dict):
        raise HistoryEvidenceError("History worker output must be a JSON object.")
    return cast(dict[str, JsonValue], payload)


def history_matrix_json(matrix: Any) -> str:
    """Render an already addressed, valid matrix as stable human-readable JSON."""

    validated = validate_history_matrix(matrix)
    return (
        json.dumps(
            validated,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _run_identity(run: dict[str, Any], index: int) -> tuple[str, str]:
    _require_exact_keys(run, _RUN_KEYS, f"runs[{index}]")
    case_id = _require_nonempty_string(run.get("case_id"), f"runs[{index}].case_id")
    sdk_version = _require_nonempty_string(run.get("sdk_version"), f"runs[{index}].sdk_version")
    return case_id, sdk_version


def _validate_run(
    run: dict[str, Any],
    spec: _RunSpec,
    tool_version: str,
    index: int,
) -> tuple[str, str]:
    location = f"runs[{index}]"
    worker = _require_object(run.get("worker"), f"{location}.worker")
    _require_exact_keys(worker, _WORKER_KEYS, f"{location}.worker")
    if worker.get("schema_version") != WORKER_SCHEMA_VERSION:
        raise HistoryEvidenceError(f"{location}.worker has an unsupported schema version.")
    if worker.get("agentrunproof_version") != tool_version:
        raise HistoryEvidenceError(
            f"{location}.worker version does not match the matrix wheel version."
        )
    if worker.get("openai_agents_version") != spec.sdk_version:
        raise HistoryEvidenceError(
            f"{location}.worker SDK version does not match its pinned matrix run."
        )
    python_version = _require_nonempty_string(worker.get("python"), f"{location}.worker.python")
    if not _PYTHON_VERSION_PATTERN.fullmatch(python_version):
        raise HistoryEvidenceError(f"{location}.worker.python must be a release version.")

    result = _require_object(worker.get("result"), f"{location}.worker.result")
    _require_exact_keys(result, _RESULT_KEYS, f"{location}.worker.result")
    if result.get("case_id") != spec.result_case_id:
        raise HistoryEvidenceError(f"{location} contains the wrong worker case identity.")
    _expect_exact(result.get("case_revision"), 1, f"{location}.worker.result.case_revision")
    _expect_exact(result.get("public_api_only"), True, f"{location}.worker.result.public_api_only")

    variants = _require_object(result.get("variants"), f"{location}.worker.result.variants")
    _require_exact_keys(variants, set(_VARIANTS), f"{location}.worker.result.variants")
    statuses: list[str] = []
    for variant_name in _VARIANTS:
        variant = _require_object(
            variants[variant_name],
            f"{location}.worker.result.variants.{variant_name}",
        )
        if spec.case_id == "session-limit-orphan-output":
            status = _validate_session_variant(
                variant,
                fingerprint=spec.fingerprint,
                location=f"{location}.{variant_name}",
            )
        elif spec.case_id == "runstate-context-approval":
            status = _validate_approval_variant(
                variant,
                fingerprint=spec.fingerprint,
                location=f"{location}.{variant_name}",
            )
        else:
            status = _validate_guardrail_variant(
                variant,
                fingerprint=spec.fingerprint,
                variant_name=variant_name,
                location=f"{location}.{variant_name}",
            )
        statuses.append(status)

    recomputed_overall = "PASS" if statuses == ["PASS", "PASS"] else "FAIL"
    if result.get("overall_status") != recomputed_overall:
        raise HistoryEvidenceError(
            f"{location}.worker.result.overall_status contradicts raw observations."
        )
    if run.get("expected") != spec.expected:
        raise HistoryEvidenceError(f"{location}.expected does not match the pinned run contract.")
    if run.get("observed") != recomputed_overall:
        raise HistoryEvidenceError(f"{location}.observed contradicts raw observations.")
    expected_exit = 0 if recomputed_overall == "PASS" else 1
    _expect_exact(run.get("worker_exit"), expected_exit, f"{location}.worker_exit")
    if recomputed_overall != spec.expected:
        raise HistoryEvidenceError(f"{location} does not match its historical fingerprint.")
    return recomputed_overall, python_version


def _validate_session_variant(
    value: dict[str, Any],
    *,
    fingerprint: str,
    location: str,
) -> str:
    _require_exact_keys(value, _SESSION_VARIANT_KEYS, location)
    model_input = value.get("model_input")
    observed_types = _item_types(model_input)
    orphan_ids = _orphan_output_ids(model_input)
    _expect_exact(value.get("observed_types"), observed_types, f"{location}.observed_types")
    _expect_exact(value.get("orphan_call_ids"), orphan_ids, f"{location}.orphan_call_ids")

    exception_type = value.get("exception_type")
    if exception_type is not None and not isinstance(exception_type, str):
        raise HistoryEvidenceError(f"{location}.exception_type must be a string or null.")
    passed = exception_type is None and orphan_ids == [] and observed_types == ["message", "user"]
    status = "PASS" if passed else "FAIL"
    reason = "OK" if passed else "ORPHAN_FUNCTION_OUTPUT_REACHED_MODEL"
    _require_summary(value, status=status, reason=reason, location=location)

    expected_input: JsonValue
    if fingerprint == "buggy":
        expected_input = [_SESSION_ORPHAN_OUTPUT, _SESSION_MESSAGE, _SESSION_USER]
        expected_status = "FAIL"
    else:
        expected_input = [_SESSION_MESSAGE, _SESSION_USER]
        expected_status = "PASS"
    _expect_exact(model_input, expected_input, f"{location}.model_input fingerprint")
    _expect_exact(exception_type, None, f"{location}.exception_type fingerprint")
    if status != expected_status:
        raise HistoryEvidenceError(f"{location} does not match the pinned #4322 fingerprint.")
    return status


def _validate_approval_variant(
    value: dict[str, Any],
    *,
    fingerprint: str,
    location: str,
) -> str:
    _require_exact_keys(value, _APPROVAL_VARIANT_KEYS, location)
    invocations = value.get("tool_invocations")
    if not isinstance(invocations, list):
        raise HistoryEvidenceError(f"{location}.tool_invocations must be a JSON array.")
    invocation_count = value.get("tool_invocation_count")
    _require_nonnegative_int(invocation_count, f"{location}.tool_invocation_count")
    _expect_exact(invocation_count, len(invocations), f"{location}.tool_invocation_count")

    restored_ids = value.get("restored_interruption_call_ids")
    approved_call_id: JsonValue = (
        restored_ids[0] if isinstance(restored_ids, list) and len(restored_ids) == 1 else None
    )
    _expect_exact(value.get("approved_call_id"), approved_call_id, f"{location}.approved_call_id")

    reason = _approval_reason(value)
    status = "PASS" if reason == "OK" else "FAIL"
    _require_summary(value, status=status, reason=reason, location=location)

    expected_raw: dict[str, JsonValue] = {
        "initial_interruption_call_ids": [_APPROVAL_CALL_ID],
        "restored_interruption_call_ids": [_APPROVAL_CALL_ID],
        "approved_call_id": _APPROVAL_CALL_ID,
        "initial_model_call_count": 1,
        "state_json_round_trip": True,
        "exception_type": None,
    }
    if fingerprint == "buggy":
        expected_raw.update(
            {
                "resumed_interruption_call_ids": [_APPROVAL_CALL_ID],
                "tool_invocations": [],
                "tool_invocation_count": 0,
                "final_output": None,
                "state_schema_version": "1.13",
                "resumed_model_call_count": 0,
                "resumed_model_remaining_steps": 1,
            }
        )
        expected_status = "FAIL"
    else:
        expected_raw.update(
            {
                "resumed_interruption_call_ids": [],
                "tool_invocations": [_APPROVAL_INVOCATION],
                "tool_invocation_count": 1,
                "final_output": "done",
                "state_schema_version": "1.15",
                "resumed_model_call_count": 1,
                "resumed_model_remaining_steps": 0,
            }
        )
        expected_status = "PASS"
    for key, expected in expected_raw.items():
        _expect_exact(value.get(key), expected, f"{location}.{key} fingerprint")
    if status != expected_status:
        raise HistoryEvidenceError(f"{location} does not match the pinned #4244 fingerprint.")
    return status


def _approval_reason(value: dict[str, Any]) -> str:
    if value.get("exception_type") is not None:
        return "SCENARIO_EXCEPTION"
    if not _same_json(value.get("initial_interruption_call_ids"), [_APPROVAL_CALL_ID]):
        return "INITIAL_APPROVAL_INTERRUPTION_MISSING"
    if value.get("state_json_round_trip") is not True:
        return "RUNSTATE_JSON_ROUND_TRIP_CHANGED"
    if not _same_json(value.get("restored_interruption_call_ids"), [_APPROVAL_CALL_ID]):
        return "RESTORED_APPROVAL_INTERRUPTION_MISSING"
    resumed_ids = value.get("resumed_interruption_call_ids")
    if not _same_json(resumed_ids, []):
        return "APPROVAL_NOT_HONORED_WITH_CONTEXT_OVERRIDE"
    invocations = value.get("tool_invocations")
    if not isinstance(invocations, list) or len(invocations) != 1:
        return "APPROVED_TOOL_NOT_INVOKED_EXACTLY_ONCE"
    invocation = invocations[0]
    if not isinstance(invocation, dict) or invocation.get("call_id") != _APPROVAL_CALL_ID:
        return "APPROVED_TOOL_CALL_ID_CHANGED"
    if invocation.get("principal") != "ella":
        return "CONTEXT_OVERRIDE_NOT_VISIBLE_TO_TOOL"
    if not _same_json(invocation.get("arguments"), {}):
        return "APPROVED_TOOL_ARGUMENTS_CHANGED"
    if value.get("final_output") != "done":
        return "RESUMED_FINAL_OUTPUT_MISMATCH"
    if value.get("resumed_model_remaining_steps") != 0:
        return "RESUMED_MODEL_STEP_NOT_CONSUMED"
    return "OK"


def _validate_guardrail_variant(
    value: dict[str, Any],
    *,
    fingerprint: str,
    variant_name: str,
    location: str,
) -> str:
    _require_exact_keys(value, _GUARDRAIL_VARIANT_KEYS, location)
    session_after_tripwire = value.get("session_after_tripwire")
    followup_model_input = value.get("followup_model_input")
    durable_sequence = _tool_sequence(session_after_tripwire)
    replayed_sequence = _tool_sequence(followup_model_input)
    _expect_exact(
        value.get("durable_tool_sequence"),
        durable_sequence,
        f"{location}.durable_tool_sequence",
    )
    _expect_exact(
        value.get("replayed_tool_sequence"),
        replayed_sequence,
        f"{location}.replayed_tool_sequence",
    )

    violations = _guardrail_violations(value, durable_sequence, replayed_sequence)
    status = "PASS" if not violations else "FAIL"
    reason = "OK" if not violations else violations[0]
    _expect_exact(value.get("violations"), violations, f"{location}.violations")
    _require_summary(value, status=status, reason=reason, location=location)

    buggy_streaming = fingerprint == "buggy" and variant_name == "streaming"
    if buggy_streaming:
        tripwire_session: JsonValue = [_GUARDRAIL_USER, _GUARDRAIL_CALL]
        followup_input: JsonValue = [_GUARDRAIL_USER, _GUARDRAIL_CONTINUE]
        followup_session: JsonValue = [
            _GUARDRAIL_USER,
            _GUARDRAIL_CALL,
            _GUARDRAIL_CONTINUE,
            _GUARDRAIL_MESSAGE,
        ]
        expected_status = "FAIL"
    else:
        tripwire_session = [_GUARDRAIL_USER, _GUARDRAIL_CALL, _GUARDRAIL_OUTPUT]
        followup_input = [
            _GUARDRAIL_USER,
            _GUARDRAIL_CALL,
            _GUARDRAIL_OUTPUT,
            _GUARDRAIL_CONTINUE,
        ]
        followup_session = [
            _GUARDRAIL_USER,
            _GUARDRAIL_CALL,
            _GUARDRAIL_OUTPUT,
            _GUARDRAIL_CONTINUE,
            _GUARDRAIL_MESSAGE,
        ]
        expected_status = "PASS"

    expected_raw: dict[str, JsonValue] = {
        "interruption_count": 1,
        "resume_exception_type": "OutputGuardrailTripwireTriggered",
        "side_effect_count": 1,
        "session_after_tripwire": tripwire_session,
        "session_after_followup": followup_session,
        "followup_model_input": followup_input,
        "followup_final_output": "done",
        "first_exception_type": None,
        "followup_exception_type": None,
    }
    for key, expected in expected_raw.items():
        _expect_exact(value.get(key), expected, f"{location}.{key} fingerprint")
    if status != expected_status:
        raise HistoryEvidenceError(f"{location} does not match the pinned #4125 fingerprint.")
    return status


def _guardrail_violations(
    value: dict[str, Any],
    durable_sequence: list[JsonValue],
    replayed_sequence: list[JsonValue],
) -> list[str]:
    violations: list[str] = []
    if value.get("first_exception_type") is not None or value.get("interruption_count") != 1:
        violations.append("EXPECTED_APPROVAL_INTERRUPTION_MISSING")
    if value.get("resume_exception_type") != "OutputGuardrailTripwireTriggered":
        violations.append("RESUME_DID_NOT_TRIP_OUTPUT_GUARDRAIL")
    if value.get("side_effect_count") != 1:
        violations.append("SIDE_EFFECT_COUNT_NOT_ONE")
    if not _same_json(durable_sequence, _GUARDRAIL_TOOL_PAIR):
        violations.append("COMMITTED_TOOL_PAIR_NOT_DURABLE")
    if not _same_json(replayed_sequence, _GUARDRAIL_TOOL_PAIR):
        violations.append("FOLLOWUP_MODEL_INPUT_MISSING_TOOL_PAIR")
    if (
        value.get("followup_exception_type") is not None
        or value.get("followup_final_output") != "done"
    ):
        violations.append("FOLLOWUP_DID_NOT_COMPLETE")
    return violations


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


def _tool_sequence(items: Any) -> list[JsonValue]:
    if not isinstance(items, list):
        return []
    result: list[JsonValue] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type not in {"function_call", "function_call_output"}:
            continue
        normalized: dict[str, JsonValue] = {"type": cast(str, item_type)}
        call_id = item.get("call_id")
        if isinstance(call_id, str):
            normalized["call_id"] = call_id
        if item_type == "function_call_output":
            output = item.get("output")
            if _is_json_value(output):
                normalized["output"] = cast(JsonValue, copy.deepcopy(output))
            else:
                normalized["output"] = None
        result.append(normalized)
    return result


def _require_summary(
    value: dict[str, Any],
    *,
    status: str,
    reason: str,
    location: str,
) -> None:
    if value.get("status") != status:
        raise HistoryEvidenceError(f"{location}.status contradicts raw observations.")
    if value.get("reason") != reason:
        raise HistoryEvidenceError(f"{location}.reason contradicts raw observations.")


def _matrix_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise HistoryEvidenceError("The history matrix must be a JSON object.")
    unsigned = copy.deepcopy(payload)
    unsigned["matrix_id"] = None
    try:
        return f"sha256:{sha256_hex(unsigned)}"
    except CanonicalizationError as error:
        raise HistoryEvidenceError(f"History matrix is not canonical JSON: {error}") from error


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HistoryEvidenceError(f"Duplicate JSON object key: {key!r}.")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> None:
    raise HistoryEvidenceError(f"Non-finite JSON number is forbidden: {value}.")


def _require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HistoryEvidenceError(f"{location} must be a JSON object.")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], location: str) -> None:
    if not all(isinstance(key, str) for key in value):
        raise HistoryEvidenceError(f"{location} requires string field names.")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise HistoryEvidenceError(
            f"{location} has unexpected fields; missing={missing}, extra={extra}."
        )


def _require_nonempty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise HistoryEvidenceError(f"{location} must be a non-empty string.")
    return value


def _require_nonnegative_int(value: Any, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HistoryEvidenceError(f"{location} must be a non-negative integer.")
    return value


def _expect_exact(value: Any, expected: Any, location: str) -> None:
    if not _same_json(value, expected):
        raise HistoryEvidenceError(f"{location} does not match the frozen evidence contract.")


def _same_json(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(_same_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return bool(left == right)


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, bool | int | float | str):
        return not isinstance(value, float) or value == value and abs(value) != float("inf")
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False
