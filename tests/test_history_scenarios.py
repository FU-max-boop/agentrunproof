from __future__ import annotations

import json

import pytest

from agentrunproof.history.scenarios import SCENARIOS, runstate_context_approval


@pytest.mark.asyncio
async def test_runstate_context_approval_passes_on_fixed_sdk_and_is_json_serializable() -> None:
    result = await runstate_context_approval()

    assert result["overall_status"] == "PASS"
    assert result["public_api_only"] is True
    assert set(result["variants"]) == {"non_streaming", "streaming"}
    for observation in result["variants"].values():
        assert observation["status"] == "PASS"
        assert observation["reason"] == "OK"
        assert observation["approved_call_id"] == "approval-call-1"
        assert observation["resumed_interruption_call_ids"] == []
        assert observation["tool_invocation_count"] == 1
        assert observation["tool_invocations"] == [
            {"arguments": {}, "call_id": "approval-call-1", "principal": "ella"}
        ]
        assert observation["final_output"] == "done"
        assert observation["resumed_model_call_count"] == 1
        assert observation["resumed_model_remaining_steps"] == 0

    json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True)


def test_runstate_context_approval_is_registered_for_the_history_worker() -> None:
    assert SCENARIOS["runstate-context-approval"] is runstate_context_approval
