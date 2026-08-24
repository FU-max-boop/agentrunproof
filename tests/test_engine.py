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
    HANDOFF_SESSION_FILTERED_VIEW_PARITY,
    RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_ROUTING,
    RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_SERIALIZATION,
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


@pytest.mark.asyncio
async def test_handoff_session_scenario_binds_filtered_view_and_durable_history() -> None:
    proof = await run_scenario(HANDOFF_SESSION_FILTERED_VIEW_PARITY)

    assert proof.status == "PASS"
    assert [result.name for result in proof.invariant_results] == [
        "execution_outcome",
        "phase_contract",
        "stream_parity",
        "tool_linkage",
        "exactly_once",
        "model_script_consumed",
        "session_replay",
    ]
    assert all(result.status == "PASS" for result in proof.invariant_results)
    for observation in proof.observations.values():
        assert observation.final_output == "resolved-alpha"
        assert observation.last_agent == "Domain Specialist"
        assert observation.tool_counts == {"lookup_case": 1}
        assert len(observation.model_calls) == 3
        phase = observation.phases[0]
        assert phase.probes_after["guardrail_configuration"] == {
            "source_input": 1,
            "source_output": 1,
            "target_input": 1,
            "target_output": 1,
        }
        guardrail_events = phase.probes_after["guardrail_events"]
        assert guardrail_events[0]["stage"] == "input"
        assert guardrail_events[0]["agent"] == "Triage Agent"
        assert "prior-tool-alpha" in guardrail_events[0]["payload"]
        assert "current-user-alpha" in guardrail_events[0]["payload"]
        assert guardrail_events[1] == {
            "stage": "output",
            "agent": "Domain Specialist",
            "payload": "resolved-alpha",
        }
        assert phase.probes_after["model_tool_visibility"][-1] == []
        assert phase.probes_after["session_tool_history"] == [
            "function_call",
            "function_call_output",
            "function_call",
            "function_call_output",
        ]
        assert phase.probes_after["specialist_context"] == {
            "prior-user-alpha": 1,
            "prior-answer-alpha": 1,
            "current-user-alpha": 1,
            "route-domain-alpha": 1,
            "<CONVERSATION HISTORY>": 1,
            "prior_lookup": 0,
            "prior-alpha": 0,
            "prior-tool-alpha": 0,
            "lookup_case": 0,
            "lookup-alpha": 0,
            "risk-low-alpha": 0,
            "transfer_to_domain_specialist": 0,
            "handoff-domain": 0,
        }
        assert phase.probes_after["wrapper_restoration"] == [True]


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

    with pytest.raises(ValueError, match="requires an exact sibling decision"):
        ResumeInput(
            source_phase="initial",
            json_round_trip=False,
            save_sibling_state=True,
        )
    with pytest.raises(ValueError, match="without new decisions"):
        ResumeInput(
            source_phase="initial",
            decisions=(Decision("call-1"),),
            json_round_trip=False,
            saved_sibling_from="fork",
        )


def test_saved_sibling_state_requires_one_prior_matching_fork() -> None:
    model = DeterministicModel([[assistant_message("unused")]])
    agent = Agent(name="saved sibling contract agent", model=model)

    def phase(phase_id: str, input_value: LiteralInput | ResumeInput) -> ScenarioPhase:
        return ScenarioPhase(
            phase_id=phase_id,
            agent=agent,
            input=input_value,
            model=model,
            model_group="saved-sibling-model",
        )

    initial = phase("initial", LiteralInput("start"))
    fork = phase(
        "fork",
        ResumeInput(
            source_phase="initial",
            json_round_trip=False,
            sibling_decisions=(Decision("call-1"),),
            save_sibling_state=True,
        ),
    )
    saved = ResumeInput(
        source_phase="initial",
        json_round_trip=False,
        saved_sibling_from="fork",
    )

    with pytest.raises(ValueError, match="earlier saving phase"):
        ScenarioPlan((initial, phase("missing", saved)))
    with pytest.raises(ValueError, match="must be resumed exactly once"):
        ScenarioPlan((initial, fork))
    with pytest.raises(ValueError, match="only once"):
        ScenarioPlan(
            (
                initial,
                fork,
                phase("first-use", saved),
                phase("second-use", saved),
            )
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


def test_recursive_agent_tool_approval_declares_two_direct_sibling_branches() -> None:
    plan = RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_ROUTING.factory(RunVariant.NON_STREAMING)

    assert isinstance(plan, ScenarioPlan)
    assert [phase.phase_id for phase in plan.phases] == [
        "initial",
        "untouched-sibling",
        "approved-sibling",
    ]
    untouched = plan.phases[1]
    approved = plan.phases[2]
    assert isinstance(untouched.input, ResumeInput)
    assert untouched.input.json_round_trip is False
    assert untouched.input.save_sibling_state is True
    assert untouched.input.sibling_decisions == (
        Decision("agentrunproof-recursive-protected-call-1"),
    )
    assert untouched.expected_outcome == ExpectedOutcome(
        kind=OutcomeKind.INTERRUPTED,
        interruption_count=1,
    )
    assert isinstance(approved.input, ResumeInput)
    assert approved.input.saved_sibling_from == "untouched-sibling"
    assert approved.expected_outcome == ExpectedOutcome()
    assert approved.expected_tool_counts_delta == {"protected_effect": 1}


def test_recursive_agent_tool_serialization_declares_one_durable_approval_resume() -> None:
    plan = RUNSTATE_RECURSIVE_AGENT_TOOL_APPROVAL_SERIALIZATION.factory(RunVariant.NON_STREAMING)

    assert isinstance(plan, ScenarioPlan)
    assert [phase.phase_id for phase in plan.phases] == ["initial", "serialized-approved"]
    approved = plan.phases[1]
    assert isinstance(approved.input, ResumeInput)
    assert approved.input.source_phase == "initial"
    assert approved.input.json_round_trip is True
    assert approved.input.decisions == (Decision("agentrunproof-recursive-protected-call-1"),)
    assert approved.expected_outcome == ExpectedOutcome()
    assert approved.expected_tool_counts_delta == {"protected_effect": 1}


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
