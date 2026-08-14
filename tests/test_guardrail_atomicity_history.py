from __future__ import annotations

from agentrunproof.history.guardrail_atomicity import resumed_guardrail_atomicity
from agentrunproof.history.scenarios import SCENARIOS


async def test_resumed_guardrail_atomicity_passes_on_supported_sdk() -> None:
    result = await resumed_guardrail_atomicity()

    assert result["overall_status"] == "PASS"
    variants = result["variants"]
    assert isinstance(variants, dict)
    for observation in variants.values():
        assert isinstance(observation, dict)
        assert observation["status"] == "PASS"
        assert observation["side_effect_count"] == 1
        assert observation["violations"] == []


def test_resumed_guardrail_atomicity_is_registered_for_the_history_worker() -> None:
    assert SCENARIOS["resumed-guardrail-atomicity"] is resumed_guardrail_atomicity
