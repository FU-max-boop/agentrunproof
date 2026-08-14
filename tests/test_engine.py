from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from agents import (
    Agent,
    GuardrailFunctionOutput,
    OutputGuardrail,
    RunContextWrapper,
    function_tool,
)

from agentrunproof._canonical import sha256_hex
from agentrunproof.builtins import (
    BASIC_TOOL_SESSION_PARITY,
    RUNSTATE_SIBLING_APPROVAL_ISOLATION,
)
from agentrunproof.engine import run_scenario
from agentrunproof.model import DeterministicModel, ModelStep, assistant_message, function_call
from agentrunproof.scenario import (
    Decision,
    ExpectedOutcome,
    LiteralInput,
    OutcomeKind,
    ResumeInput,
    RunVariant,
    Scenario,
    ScenarioCase,
    ScenarioPhase,
    ScenarioPlan,
)
from agentrunproof.session import RecordingSession


@pytest.mark.asyncio
async def test_builtin_tool_session_scenario_passes_every_invariant() -> None:
    proof = await run_scenario(BASIC_TOOL_SESSION_PARITY)

    assert proof.status == "PASS"
    assert [result.status for result in proof.invariant_results] == [
        "PASS",
        "PASS",
        "PASS",
        "PASS",
        "PASS",
    ]
    assert all(
        observation.tool_counts == {"lookup_value": 1}
        for observation in proof.observations.values()
    )
    assert all(
        observation.remaining_model_steps == 0 for observation in proof.observations.values()
    )


def test_sibling_decisions_require_one_direct_undecided_subject() -> None:
    sibling = (Decision("call-1"),)

    with pytest.raises(ValueError, match="direct resume"):
        ResumeInput(source_phase="initial", sibling_decisions=sibling)
    with pytest.raises(ValueError, match="no decisions"):
        ResumeInput(
            source_phase="initial",
            decisions=(Decision("call-1"),),
            json_round_trip=False,
            sibling_decisions=sibling,
        )
    with pytest.raises(ValueError, match="unique call IDs"):
        ResumeInput(
            source_phase="initial",
            json_round_trip=False,
            sibling_decisions=(Decision("call-1"), Decision("call-1")),
        )


@pytest.mark.asyncio
async def test_runstate_sibling_isolation_records_exact_public_state_fork() -> None:
    proof = await run_scenario(RUNSTATE_SIBLING_APPROVAL_ISOLATION)
    invariant = next(
        result for result in proof.invariant_results if result.name == "state_fork_isolation"
    )
    call_digest = sha256_hex("agentrunproof-sibling-call-1")
    unchanged: list[bool] = []

    for variant, observation in proof.observations.items():
        assert len(observation.phases) == 2, variant
        initial, fork = observation.phases
        assert initial.interruption_count == 1
        transition = fork.state_transition
        assert transition["source_interruption_call_ids"] == [call_digest]
        assert transition["restored_interruption_call_ids"] == [call_digest]
        assert transition["sibling_interruption_call_ids"] == [call_digest]
        assert transition["decisions"] == []
        assert transition["sibling_decisions"] == [
            {"action": "approve", "call_id_sha256": call_digest, "matched": True}
        ]
        before = transition["subject_state_before_sha256"]
        after = transition["subject_state_after_sha256"]
        assert isinstance(before, str) and len(before) == 64
        assert isinstance(after, str) and len(after) == 64
        observed_unchanged = transition["subject_state_unchanged"]
        assert observed_unchanged is (before == after)
        assert isinstance(observed_unchanged, bool)
        unchanged.append(observed_unchanged)

        contract = proof.phase_contracts[variant][1]
        assert contract.expected_outcome == ExpectedOutcome(
            kind=OutcomeKind.INTERRUPTED,
            interruption_count=1,
        )
        assert contract.expected_tool_counts_delta == {"approval_tool": 0}

    assert len(set(unchanged)) == 1
    if all(unchanged):
        assert invariant.status == "PASS"
        assert invariant.reason == "OK"
        assert proof.status == "PASS"
    else:
        assert invariant.status == "FAIL"
        assert invariant.reason == "SIBLING_STATE_MUTATED"
        assert proof.status == "FAIL"


def test_one_model_group_cannot_hide_an_independent_unconsumed_script() -> None:
    first = DeterministicModel([[assistant_message("unused")]])
    second = DeterministicModel([[assistant_message("done")]])

    with pytest.raises(ValueError, match="sharing a model_group"):
        ScenarioPlan(
            phases=(
                ScenarioPhase(
                    phase_id="first",
                    agent=Agent(name="first", model=first),
                    input=LiteralInput("start"),
                    model=first,
                ),
                ScenarioPhase(
                    phase_id="second",
                    agent=Agent(name="second", model=second),
                    input=LiteralInput("continue"),
                    model=second,
                ),
            )
        )


@pytest.mark.asyncio
async def test_stream_parity_reports_the_differing_field(
    scenario_factory: Callable[..., Scenario],
) -> None:
    scenario = scenario_factory(non_streaming_output="left", streaming_output="right")
    proof = await run_scenario(scenario)

    assert proof.status == "FAIL"
    parity = proof.invariant_results[1]
    assert parity.reason == "VARIANT_MISMATCH"
    assert "final_output" in parity.details["differing_fields"]


@pytest.mark.asyncio
async def test_unknown_invariant_is_not_silently_passed(
    scenario_factory: Callable[..., Scenario],
) -> None:
    proof = await run_scenario(scenario_factory(invariants=("execution_outcome", "unknown-check")))

    assert proof.status == "NOT_RUN"
    assert proof.invariant_results[1].status == "NOT_RUN"
    assert proof.invariant_results[1].reason == "UNKNOWN_INVARIANT"


@pytest.mark.asyncio
async def test_session_replay_without_persisted_tool_history_is_not_run(
    scenario_factory: Callable[..., Scenario],
) -> None:
    proof = await run_scenario(scenario_factory(invariants=("execution_outcome", "session_replay")))

    assert proof.status == "NOT_RUN"
    assert proof.invariant_results[1].status == "NOT_RUN"
    assert proof.invariant_results[1].reason == "NO_PERSISTED_TOOL_HISTORY_TO_COMPARE"


@pytest.mark.asyncio
async def test_exactly_once_mismatch_fails(
    scenario_factory: Callable[..., Scenario],
) -> None:
    proof = await run_scenario(
        scenario_factory(
            invariants=("execution_outcome", "exactly_once"),
            expected_tool_counts={"expected_tool": 1},
        )
    )

    assert proof.status == "FAIL"
    assert proof.invariant_results[1].reason == "SIDE_EFFECT_COUNT_MISMATCH"


@pytest.mark.asyncio
async def test_unexpected_runner_exceptions_are_never_counted_as_success() -> None:
    def build(variant: RunVariant) -> ScenarioCase:
        del variant
        model = DeterministicModel([ModelStep(error=RuntimeError("synthetic failure"))])
        return ScenarioCase(
            agent=Agent(name="failing agent", model=model),
            input="public synthetic input",
            model=model,
        )

    scenario = Scenario(
        scenario_id="expected-failure-regression",
        revision=1,
        description="Equal unexpected failures must not pass parity.",
        variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
        invariants=("execution_outcome", "stream_parity", "model_script_consumed"),
        factory=build,
        public_payloads=True,
    )

    proof = await run_scenario(scenario)

    assert proof.status == "FAIL"
    assert proof.invariant_results[0].reason == "UNEXPECTED_EXECUTION_OUTCOME"
    assert proof.invariant_results[-1].status == "PASS"


@pytest.mark.asyncio
async def test_approval_interruptions_are_normalized_without_becoming_errors() -> None:
    @function_tool(needs_approval=True)
    def protected_tool() -> str:
        return "not executed before approval"

    def build(variant: RunVariant) -> ScenarioCase:
        del variant
        model = DeterministicModel([[function_call("protected_tool", {}, call_id="approval-call")]])
        return ScenarioCase(
            agent=Agent(name="approval agent", model=model, tools=[protected_tool]),
            input="Request the protected tool.",
            model=model,
            session=RecordingSession(),
        )

    scenario = Scenario(
        scenario_id="approval-normalization",
        revision=1,
        description="Pending approvals remain observable without conversion to model input.",
        variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
        invariants=("execution_outcome", "stream_parity", "model_script_consumed"),
        factory=build,
        expected_outcome=ExpectedOutcome(
            kind=OutcomeKind.INTERRUPTED,
            interruption_count=1,
        ),
        public_payloads=True,
    )

    proof = await run_scenario(scenario)

    assert proof.status == "PASS"
    for observation in proof.observations.values():
        assert observation.interruption_count == 1
        assert observation.new_items[0]["type"] == "function_call"


@pytest.mark.asyncio
async def test_observation_normalization_failure_is_captured_fail_closed(
    scenario_factory: Callable[..., Scenario], monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentrunproof.engine as engine_module

    def fail_capture(**kwargs: object) -> None:
        del kwargs
        raise TypeError("synthetic normalization failure")

    monkeypatch.setattr(engine_module, "capture_observation", fail_capture)

    proof = await run_scenario(scenario_factory())

    assert proof.status == "FAIL"
    assert proof.invariant_results[0].reason == "UNEXPECTED_EXECUTION_OUTCOME"
    assert all(item.exception is not None for item in proof.observations.values())


@pytest.mark.asyncio
async def test_declared_runner_exception_can_be_a_successful_outcome() -> None:
    def build(variant: RunVariant) -> ScenarioCase:
        del variant
        model = DeterministicModel([ModelStep(error=RuntimeError("expected"))])
        return ScenarioCase(
            agent=Agent(name="expected error agent", model=model),
            input="public input",
            model=model,
        )

    scenario = Scenario(
        scenario_id="expected-runner-exception",
        revision=1,
        description="A scenario can explicitly require a named Runner exception.",
        variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
        invariants=("execution_outcome", "model_script_consumed"),
        factory=build,
        expected_outcome=ExpectedOutcome(
            kind=OutcomeKind.RAISES,
            exception_type="RuntimeError",
        ),
        public_payloads=True,
    )

    proof = await run_scenario(scenario)

    assert proof.status == "PASS"
    assert all(
        observation.exception["origin"] == "runner"
        for observation in proof.observations.values()
        if observation.exception is not None
    )


@pytest.mark.asyncio
async def test_observation_error_cannot_satisfy_expected_runner_exception(
    scenario_factory: Callable[..., Scenario], monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentrunproof.engine as engine_module

    def fail_capture(**kwargs: object) -> None:
        del kwargs
        raise TypeError("observer failed")

    monkeypatch.setattr(engine_module, "capture_observation", fail_capture)
    base = scenario_factory(invariants=("execution_outcome",))
    scenario = Scenario(
        scenario_id=base.scenario_id,
        revision=base.revision,
        description=base.description,
        variants=base.variants,
        invariants=base.invariants,
        factory=base.factory,
        expected_outcome=ExpectedOutcome(
            kind=OutcomeKind.RAISES,
            exception_type="TypeError",
        ),
        public_payloads=True,
    )

    proof = await run_scenario(scenario)

    assert proof.status == "FAIL"
    assert proof.invariant_results[0].reason == "UNEXPECTED_EXECUTION_OUTCOME"
    assert all(
        observation.exception["origin"] == "observation"
        for observation in proof.observations.values()
        if observation.exception is not None
    )


@pytest.mark.asyncio
async def test_stream_parity_compares_system_instructions() -> None:
    def build(variant: RunVariant) -> ScenarioCase:
        model = DeterministicModel([[assistant_message("done")]])
        return ScenarioCase(
            agent=Agent(
                name="instruction agent",
                instructions=f"variant:{variant.value}",
                model=model,
            ),
            input="public input",
            model=model,
        )

    scenario = Scenario(
        scenario_id="system-instruction-parity",
        revision=1,
        description="System instructions are part of the model-boundary input.",
        variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
        invariants=("execution_outcome", "stream_parity", "model_script_consumed"),
        factory=build,
        public_payloads=True,
    )

    proof = await run_scenario(scenario)

    assert proof.status == "FAIL"
    assert "model_calls" in proof.invariant_results[1].details["differing_fields"]


@pytest.mark.asyncio
async def test_stream_parity_compares_guardrail_output_info() -> None:
    def build(variant: RunVariant) -> ScenarioCase:
        def guardrail(
            _context: RunContextWrapper[Any],
            _agent: Agent[Any],
            _output: Any,
        ) -> GuardrailFunctionOutput:
            return GuardrailFunctionOutput(
                output_info={"variant": variant.value},
                tripwire_triggered=False,
            )

        model = DeterministicModel([[assistant_message("done")]])
        return ScenarioCase(
            agent=Agent(
                name="guardrail agent",
                model=model,
                output_guardrails=[OutputGuardrail(guardrail_function=guardrail)],
            ),
            input="public input",
            model=model,
        )

    scenario = Scenario(
        scenario_id="guardrail-output-info-parity",
        revision=1,
        description="Guardrail evidence is part of the runtime result.",
        variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
        invariants=("execution_outcome", "stream_parity", "model_script_consumed"),
        factory=build,
        public_payloads=True,
    )

    proof = await run_scenario(scenario)

    assert proof.status == "FAIL"
    assert "guardrails" in proof.invariant_results[1].details["differing_fields"]
