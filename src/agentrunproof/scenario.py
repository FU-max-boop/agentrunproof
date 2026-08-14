from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agents import Agent, RunConfig
from agents.items import TResponseInputItem

from .model import DeterministicModel
from .session import RecordingSession


class RunVariant(str, Enum):
    NON_STREAMING = "non_streaming"
    STREAMING = "streaming"


class OutcomeKind(str, Enum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    RAISES = "raises"


@dataclass(frozen=True)
class ExpectedOutcome:
    kind: OutcomeKind = OutcomeKind.COMPLETED
    interruption_count: int = 0
    exception_type: str | None = None

    def __post_init__(self) -> None:
        if self.kind is OutcomeKind.COMPLETED:
            valid = self.interruption_count == 0 and self.exception_type is None
        elif self.kind is OutcomeKind.INTERRUPTED:
            valid = self.interruption_count >= 1 and self.exception_type is None
        else:
            valid = (
                self.interruption_count == 0
                and isinstance(self.exception_type, str)
                and bool(self.exception_type)
            )
        if not valid:
            raise ValueError(f"Invalid expected outcome contract for {self.kind.value}.")


class DecisionAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True)
class Decision:
    """An exact approval decision applied to one interrupted tool call."""

    call_id: str
    action: DecisionAction = DecisionAction.APPROVE
    rejection_message: str | None = None

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("A decision requires a non-empty call_id.")
        if self.action is DecisionAction.APPROVE and self.rejection_message is not None:
            raise ValueError("An approval decision cannot carry a rejection message.")


@dataclass(frozen=True)
class LiteralInput:
    value: str | list[TResponseInputItem]


@dataclass(frozen=True)
class ResumeInput:
    """Resume a prior phase, optionally through the public RunState JSON boundary."""

    source_phase: str
    decisions: tuple[Decision, ...] = ()
    json_round_trip: bool = True
    sibling_decisions: tuple[Decision, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_phase:
            raise ValueError("A resume input requires a source phase.")
        identifiers = [decision.call_id for decision in self.decisions]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Resume decisions must target unique call IDs.")
        sibling_identifiers = [decision.call_id for decision in self.sibling_decisions]
        if len(set(sibling_identifiers)) != len(sibling_identifiers):
            raise ValueError("Sibling decisions must target unique call IDs.")
        if self.sibling_decisions and (self.json_round_trip or self.decisions):
            raise ValueError(
                "Sibling decisions require a direct resume with no decisions on the subject state."
            )


PhaseInput = LiteralInput | ResumeInput
PhaseHook = Callable[[], None | Awaitable[None]]


@dataclass(frozen=True)
class StateProbe:
    """Capture one scenario-owned observable and bind its expected post-phase value."""

    name: str
    capture: Callable[[], Any]
    expected_after: Any

    def __post_init__(self) -> None:
        if not self.name or any(character.isspace() for character in self.name):
            raise ValueError("A probe name must be a non-empty token without whitespace.")


@dataclass
class ScenarioCase:
    agent: Agent[Any]
    input: str | list[TResponseInputItem]
    model: DeterministicModel
    session: RecordingSession | None = None
    run_config: RunConfig | None = None
    max_turns: int | None = None
    tool_counts: dict[str, int] = field(default_factory=dict)
    context: Any = None


@dataclass
class ScenarioPhase:
    phase_id: str
    agent: Agent[Any]
    input: PhaseInput
    model: DeterministicModel
    session: RecordingSession | None = None
    run_config: RunConfig | None = None
    max_turns: int | None = None
    tool_counts: dict[str, int] = field(default_factory=dict)
    context: Any = None
    expected_outcome: ExpectedOutcome = field(default_factory=ExpectedOutcome)
    expected_tool_counts_delta: Mapping[str, int] = field(default_factory=dict)
    probes: tuple[StateProbe, ...] = ()
    before: PhaseHook | None = None
    model_group: str = "default"

    def __post_init__(self) -> None:
        if not self.phase_id or any(character.isspace() for character in self.phase_id):
            raise ValueError("phase_id must be a non-empty token without whitespace.")
        if not self.model_group or any(character.isspace() for character in self.model_group):
            raise ValueError("model_group must be a non-empty token without whitespace.")
        if len({probe.name for probe in self.probes}) != len(self.probes):
            raise ValueError("Phase probe names must be unique.")
        if any(
            not isinstance(count, int) or isinstance(count, bool) or count < 0
            for count in self.expected_tool_counts_delta.values()
        ):
            raise ValueError("Expected phase tool deltas must be non-negative integers.")


@dataclass(frozen=True)
class ScenarioPlan:
    phases: tuple[ScenarioPhase, ...]

    def __post_init__(self) -> None:
        if not self.phases:
            raise ValueError("A scenario plan requires at least one phase.")
        phase_ids = [phase.phase_id for phase in self.phases]
        if len(set(phase_ids)) != len(phase_ids):
            raise ValueError("Scenario phase IDs must be unique.")
        seen: set[str] = set()
        models_by_group: dict[str, DeterministicModel] = {}
        for phase in self.phases:
            if isinstance(phase.input, ResumeInput) and phase.input.source_phase not in seen:
                raise ValueError("A resume input must reference an earlier phase in the same plan.")
            existing_model = models_by_group.setdefault(phase.model_group, phase.model)
            if existing_model is not phase.model:
                raise ValueError(
                    "Phases sharing a model_group must share one DeterministicModel instance; "
                    "use distinct groups for independent scripts."
                )
            seen.add(phase.phase_id)


@dataclass(frozen=True)
class PhaseContract:
    phase_id: str
    input_kind: str
    source_phase: str | None
    json_round_trip: bool
    decisions: tuple[dict[str, Any], ...]
    expected_outcome: ExpectedOutcome
    expected_tool_counts_delta: Mapping[str, int]
    expected_probes_after: Mapping[str, Any]
    callback_markers: Mapping[str, Any]
    model_group: str
    sibling_decisions: tuple[dict[str, Any], ...] = ()


ScenarioFactory = Callable[[RunVariant], ScenarioCase | ScenarioPlan]


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    revision: int
    description: str
    variants: tuple[RunVariant, ...]
    invariants: tuple[str, ...]
    factory: ScenarioFactory
    expected_tool_counts: Mapping[str, int] = field(default_factory=dict)
    expected_outcome: ExpectedOutcome = field(default_factory=ExpectedOutcome)
    public_payloads: bool = False

    def __post_init__(self) -> None:
        if not self.scenario_id or any(char.isspace() for char in self.scenario_id):
            raise ValueError("scenario_id must be a non-empty token without whitespace.")
        if self.revision < 1:
            raise ValueError("scenario revision must be positive.")
        if not self.variants:
            raise ValueError("A scenario requires at least one runtime variant.")
        if len(set(self.variants)) != len(self.variants):
            raise ValueError("Scenario variants must be unique.")
        if len(set(self.invariants)) != len(self.invariants):
            raise ValueError("Scenario invariants must be unique.")
        if "execution_outcome" not in self.invariants:
            raise ValueError("A scenario must request the execution_outcome invariant.")
