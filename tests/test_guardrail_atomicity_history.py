from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentrunproof import RunVariant
from agentrunproof._canonical import JsonValue
from agentrunproof.history.guardrail_atomicity import (
    _history_observation,
    _rejected_tool_output_policy,
    _tool_output_policy_for_sdk,
    _tool_pair_matches_policy,
    resumed_guardrail_atomicity,
)
from agentrunproof.history.scenarios import SCENARIOS


async def test_resumed_guardrail_atomicity_passes_on_supported_sdk() -> None:
    result = await resumed_guardrail_atomicity()

    assert result["overall_status"] == "PASS"
    assert result["case_revision"] == (2 if _rejected_tool_output_policy() == "redacted" else 1)
    variants = result["variants"]
    assert isinstance(variants, dict)
    for observation in variants.values():
        assert isinstance(observation, dict)
        assert observation["status"] == "PASS"
        assert observation["side_effect_count"] == 1
        assert observation["violations"] == []


def test_resumed_guardrail_atomicity_is_registered_for_the_history_worker() -> None:
    assert SCENARIOS["resumed-guardrail-atomicity"] is resumed_guardrail_atomicity


def test_rejected_output_redaction_is_semantic_not_placeholder_specific() -> None:
    def sequence(output: JsonValue) -> list[dict[str, JsonValue]]:
        return [
            {"type": "function_call", "call_id": "call-approved"},
            {
                "type": "function_call_output",
                "call_id": "call-approved",
                "output": output,
            },
        ]

    assert _tool_pair_matches_policy(sequence("blocked"), policy="redacted")
    assert _tool_pair_matches_policy(
        sequence({"status": "withheld"}),
        policy="redacted",
    )
    assert not _tool_pair_matches_policy(
        sequence("approved-result"),
        policy="redacted",
    )
    assert not _tool_pair_matches_policy(
        sequence({"message": "blocked approved-result"}),
        policy="redacted",
    )
    assert not _tool_pair_matches_policy(sequence(None), policy="redacted")


def test_installed_sdk_selects_a_known_guardrail_output_policy() -> None:
    assert _rejected_tool_output_policy() in {"raw", "redacted"}


@pytest.mark.asyncio
async def test_history_result_does_not_hide_an_engine_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_proof() -> SimpleNamespace:
        return SimpleNamespace(
            status="FAIL",
            scenario=SimpleNamespace(revision=2),
            observations={RunVariant.NON_STREAMING: object()},
        )

    monkeypatch.setattr(
        "agentrunproof.history.guardrail_atomicity.resumed_guardrail_atomicity_proof",
        failed_proof,
    )
    monkeypatch.setattr(
        "agentrunproof.history.guardrail_atomicity._history_observation",
        lambda _observation: {"status": "PASS"},
    )

    result = await resumed_guardrail_atomicity()

    assert result["overall_status"] == "FAIL"


@pytest.mark.parametrize(
    ("sdk_version", "expected"),
    [
        ("0.19.2", "raw"),
        ("0.20.0", "raw"),
        ("0.21.0", "raw"),
        ("0.22.0", "redacted"),
    ],
)
def test_guardrail_output_policy_has_explicit_release_boundaries(
    sdk_version: str,
    expected: str,
) -> None:
    assert _tool_output_policy_for_sdk(sdk_version) == expected


def test_guardrail_output_policy_rejects_an_unverified_release() -> None:
    with pytest.raises(RuntimeError, match=r"outside the supported >=0\.20,<0\.23 window"):
        _tool_output_policy_for_sdk("0.23.0")


def test_raw_output_leak_has_a_redaction_specific_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentrunproof.history.guardrail_atomicity._rejected_tool_output_policy",
        lambda: "redacted",
    )
    pair = [
        {"type": "function_call", "call_id": "call-approved"},
        {
            "type": "function_call_output",
            "call_id": "call-approved",
            "output": "approved-result",
        },
    ]
    observation = SimpleNamespace(
        phases=(
            SimpleNamespace(exception=None, interruption_count=1),
            SimpleNamespace(
                exception={"type": "agents.exceptions.OutputGuardrailTripwireTriggered"},
                probes_after={"side_effects": ["ran"]},
                session_items_after=pair,
            ),
            SimpleNamespace(
                exception=None,
                final_output="done",
                model_calls=[{"input": pair}],
                session_items_after=pair,
            ),
        )
    )

    result = _history_observation(observation)

    assert result["status"] == "FAIL"
    assert result["violations"] == [
        "REJECTED_TOOL_OUTPUT_NOT_REDACTED_FROM_SESSION",
        "REJECTED_TOOL_OUTPUT_NOT_REDACTED_FROM_MODEL_INPUT",
    ]
