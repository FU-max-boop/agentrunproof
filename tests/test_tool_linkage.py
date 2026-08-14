from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from agents import Agent, function_tool
from agents.items import TResponseInputItem

from agentrunproof.engine import run_scenario
from agentrunproof.invariants import evaluate_invariants
from agentrunproof.model import DeterministicModel, assistant_message, function_call
from agentrunproof.scenario import (
    ExpectedOutcome,
    OutcomeKind,
    RunVariant,
    Scenario,
    ScenarioCase,
)
from agentrunproof.session import RecordingSession


def _completed_history_scenario(history: list[TResponseInputItem]) -> Scenario:
    def build(variant: RunVariant) -> ScenarioCase:
        del variant
        model = DeterministicModel([[assistant_message("done")]])
        return ScenarioCase(
            agent=Agent(name="linkage test agent", model=model),
            input="Continue.",
            model=model,
            session=RecordingSession(items=history),
        )

    return Scenario(
        scenario_id="tool-linkage-history",
        revision=1,
        description="Historical tool items must form unique ordered pairs.",
        variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
        invariants=("execution_outcome", "tool_linkage", "model_script_consumed"),
        factory=build,
        public_payloads=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("history", "detail_key"),
    [
        (
            [
                {"type": "function_call_output", "call_id": "A", "output": "early"},
                {"type": "function_call", "call_id": "A", "name": "tool", "arguments": "{}"},
            ],
            "out_of_order_output_ids",
        ),
        (
            [
                {"type": "function_call", "call_id": "A", "name": "tool", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "A", "output": "first"},
                {"type": "function_call", "call_id": "A", "name": "tool", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "A", "output": "second"},
            ],
            "duplicate_call_ids",
        ),
    ],
)
async def test_tool_linkage_rejects_ordering_and_identity_reuse(
    history: list[TResponseInputItem], detail_key: str
) -> None:
    proof = await run_scenario(_completed_history_scenario(history))

    assert proof.status == "FAIL"
    linkage = proof.invariant_results[1]
    assert linkage.reason == "INCOHERENT_TOOL_HISTORY"
    assert all(details[detail_key] for details in linkage.details.values())


@pytest.mark.asyncio
async def test_pending_approval_only_exempts_its_exact_call_identity() -> None:
    @function_tool(needs_approval=True)
    def protected_tool() -> str:
        return "not yet"

    def build(variant: RunVariant) -> ScenarioCase:
        del variant
        model = DeterministicModel([[function_call("protected_tool", {}, call_id="pending-call")]])
        return ScenarioCase(
            agent=Agent(name="approval linkage agent", model=model, tools=[protected_tool]),
            input="Request approval.",
            model=model,
            session=RecordingSession(
                items=[
                    {
                        "type": "function_call",
                        "call_id": "old-unfinished-call",
                        "name": "old_tool",
                        "arguments": "{}",
                    }
                ]
            ),
        )

    scenario = Scenario(
        scenario_id="pending-approval-linkage",
        revision=1,
        description="Only the currently interrupted invocation may lack an output.",
        variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
        invariants=("execution_outcome", "tool_linkage", "model_script_consumed"),
        factory=build,
        expected_outcome=ExpectedOutcome(
            kind=OutcomeKind.INTERRUPTED,
            interruption_count=1,
        ),
        public_payloads=True,
    )

    proof = await run_scenario(scenario)

    assert proof.status == "FAIL"
    linkage = proof.invariant_results[1]
    assert linkage.reason == "INCOHERENT_TOOL_HISTORY"
    assert all(details["missing_output_ids"] for details in linkage.details.values())


@pytest.mark.asyncio
async def test_tool_linkage_checks_each_model_input_not_only_final_session() -> None:
    history = [
        {
            "type": "function_call",
            "call_id": "A",
            "name": "tool",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "A", "output": "done"},
    ]
    scenario = _completed_history_scenario(history)
    proof = await run_scenario(scenario)
    assert proof.status == "PASS"

    observations = copy.deepcopy(proof.observations)
    orphan_digest = observations[RunVariant.NON_STREAMING].tool_linkage["session"][1]
    linkage = copy.deepcopy(observations[RunVariant.NON_STREAMING].tool_linkage)
    linkage["model_inputs"] = [[orphan_digest]]
    observations[RunVariant.NON_STREAMING] = replace(
        observations[RunVariant.NON_STREAMING],
        tool_linkage=linkage,
    )

    results = evaluate_invariants(scenario, observations)
    tool_linkage = results[1]
    assert tool_linkage.status == "FAIL"
    assert tool_linkage.reason == "INCOHERENT_TOOL_HISTORY"
    assert tool_linkage.details[RunVariant.NON_STREAMING.value]["orphan_output_ids"]
    assert "model_inputs[0]" in tool_linkage.details[RunVariant.NON_STREAMING.value]["channels"]
