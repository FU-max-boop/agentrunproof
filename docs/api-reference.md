# Python API reference

This page documents the import surface exported by AgentRunProof 0.3.0. Import ordinary scenario
building and certificate helpers from `agentrunproof`. The separate `agentrunproof.current`
namespace is an advanced, schema-bound API for reproducing the repository's canonical current and
released-versus-upstream evidence bundles.

The names in the two tables below are checked against each module's `__all__` in CI. Objects
returned by these functions can expose additional implementation types; those types are not
supported import paths unless they are listed here.

## Minimal provider-free scenario

```python
import asyncio
from pathlib import Path

from agents import Agent
from agentrunproof import (
    DeterministicModel,
    RunVariant,
    Scenario,
    ScenarioCase,
    assistant_message,
    build_certificate,
    run_scenario,
    write_certificate,
)


def make_case(variant: RunVariant) -> ScenarioCase:
    del variant
    model = DeterministicModel([[assistant_message("contract satisfied")]])
    agent = Agent(
        name="provider-free contract",
        instructions="Return the scripted result.",
        model=model,
    )
    return ScenarioCase(agent=agent, input="Run the contract.", model=model)


scenario = Scenario(
    scenario_id="example-contract",
    revision=1,
    description="The same scripted completion works in both Runner modes.",
    variants=(RunVariant.NON_STREAMING, RunVariant.STREAMING),
    invariants=("execution_outcome", "stream_parity", "model_script_consumed"),
    factory=make_case,
    public_payloads=True,
)

proof = asyncio.run(run_scenario(scenario))
certificate = build_certificate(proof)
write_certificate(Path("proof.json"), certificate)
assert proof.status == "PASS"
```

`run_scenario()` executes the real SDK Runner with tracing disabled. The deterministic model sends
no request to a model provider. A custom scenario's tools, hooks, session implementation, or other
dependencies are ordinary Python code and are not network-sandboxed.

## `agentrunproof` top-level API

<!-- reference-table:agentrunproof:start -->
| Name | Kind | Purpose |
| --- | --- | --- |
| `__version__` | Constant | Installed AgentRunProof version string. |
| `CertificateError` | Exception | Raised for malformed, inconsistent, tampered, or unreadable certificate data. |
| `Decision` | Frozen data class | Binds one interrupted tool `call_id` to an approval or rejection decision. |
| `DecisionAction` | String enum | Decision values `APPROVE` and `REJECT`. |
| `DeterministicModel` | Model implementation | Replays scripted `ModelStep` values through the public SDK model interface and records model calls. |
| `ExpectedOutcome` | Frozen data class | Declares a completed, interrupted, or exception outcome for a scenario or phase. |
| `LiteralInput` | Frozen data class | Wraps literal string or response-item input for a `ScenarioPhase`. |
| `ModelStep` | Frozen data class | Declares one model output tuple or one raised exception, plus usage and response ID metadata. |
| `OutcomeKind` | String enum | Expected outcome values `COMPLETED`, `INTERRUPTED`, and `RAISES`. |
| `ResumeInput` | Frozen data class | Resumes an earlier phase, optionally through JSON restoration and exact approval decisions. |
| `RecordingSession` | Session implementation | Detached in-memory SDK session with copied items and an operation log. |
| `RunVariant` | String enum | Runner paths `NON_STREAMING` and `STREAMING`. |
| `Scenario` | Frozen data class | Names a versioned contract, variants, invariants, factory, expected counts/outcome, and redaction profile. |
| `ScenarioCase` | Data class | Supplies the agent, input, model, and optional run settings for a one-phase contract. |
| `ScenarioPhase` | Data class | Declares one ordered phase, its input/expectations, optional probes and hook, and model group. |
| `ScenarioPlan` | Frozen data class | Validates and holds an ordered, non-empty tuple of `ScenarioPhase` values. |
| `StateProbe` | Frozen data class | Captures one scenario-owned post-phase value and its expected value. |
| `assistant_message` | Function | Builds a completed assistant output item from text and an optional deterministic item ID. |
| `build_certificate` | Function | Converts a completed proof run into validated certificate-v1 JSON data. |
| `function_call` | Function | Builds a function-call output item; mappings become sorted compact JSON, while string arguments pass through unchanged. |
| `load_certificate` | Function | Strictly reads and validates a certificate JSON file. |
| `run_scenario` | Async function | Runs every declared variant, records observations, evaluates invariants, and returns the proof result. |
| `validate_certificate` | Function | Revalidates schema, content address, relationships, redaction, and invariant semantics; returns a deep copy. |
| `write_certificate` | Function | Validates and atomically writes canonical, sorted UTF-8 certificate JSON with restrictive initial permissions. |
<!-- reference-table:agentrunproof:end -->

### Version, errors, and enum values

```text
__version__: str = "0.3.0"

class CertificateError(ValueError)

class DecisionAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"

class OutcomeKind(str, Enum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    RAISES = "raises"

class RunVariant(str, Enum):
    NON_STREAMING = "non_streaming"
    STREAMING = "streaming"
```

`CertificateError` is the common error raised by the certificate load, validate, build, and write
path when certificate data is malformed or semantically inconsistent. Filesystem operations can
also raise `OSError`. The enum members are strings and are the values serialized into contracts
and certificates.

### Scripted model and output helpers

```text
ModelStep(
    output: tuple[TResponseOutputItem, ...] = (),
    error: Exception | None = None,
    usage: Usage = <new Usage(requests=1)>,
    response_id: str = "agentrunproof-response",
)

DeterministicModel(
    steps: Sequence[ModelStep | Sequence[TResponseOutputItem]],
    *,
    emit_traces: bool = False,
)

assistant_message(
    text: str,
    *,
    item_id: str = "agentrunproof-message",
) -> TResponseOutputItem

function_call(
    name: str,
    arguments: str | Mapping[str, Any],
    *,
    call_id: str,
    item_id: str = "agentrunproof-tool-call",
) -> TResponseOutputItem
```

`ModelStep.output` and `ModelStep.error` are mutually exclusive; setting both raises `ValueError`.
Each omitted `usage` gets its own `Usage(requests=1)` value. `DeterministicModel` snapshots the
steps on construction and consumes one per model call. `emit_traces` must be a `bool`; the default
does not emit a generation span. A call after the script is exhausted raises a model-script
runtime error; `assert_complete()` raises when a run leaves scripted steps unconsumed.

The model exposes these observable members:

- `calls -> tuple[ModelCall, ...]` returns a deep-copied snapshot of recorded calls;
- `remaining_steps -> int` reports unconsumed scripted steps;
- `assert_complete() -> None` raises a model-script runtime error when steps remain.

`ModelCall` is the type of a `calls` entry, with `system_instructions`, `input`, `model_settings`,
`tools`, `handoffs`, `output_schema`, `previous_response_id`, `conversation_id`, `prompt`, and
`streamed` attributes. It is observable output, not a supported top-level import.

`assistant_message()` returns a completed assistant `ResponseOutputMessage`. `function_call()`
returns a `ResponseFunctionToolCall`. When `arguments` is a mapping, it is serialized as compact
JSON with sorted keys and Unicode preserved; a `str` is passed through unchanged as the
function-call argument text. The caller must supply the logical `call_id`; `item_id` identifies
the response output item.

### Scenario declarations

```text
Decision(
    call_id: str,
    action: DecisionAction = DecisionAction.APPROVE,
    rejection_message: str | None = None,
)

ExpectedOutcome(
    kind: OutcomeKind = OutcomeKind.COMPLETED,
    interruption_count: int = 0,
    exception_type: str | None = None,
)

LiteralInput(value: str | list[TResponseInputItem])

ResumeInput(
    source_phase: str,
    decisions: tuple[Decision, ...] = (),
    json_round_trip: bool = True,
    sibling_decisions: tuple[Decision, ...] = (),
    save_sibling_state: bool = False,
    saved_sibling_from: str | None = None,
)

StateProbe(
    name: str,
    capture: Callable[[], Any],
    expected_after: Any,
)
```

`Decision` requires a non-empty call ID. Approval cannot carry a rejection message. A completed
outcome requires zero interruptions and no exception; an interrupted outcome requires at least
one interruption; a raised outcome requires a non-empty exception type. Invalid combinations
raise `ValueError` at construction.

`ResumeInput.source_phase` names an earlier phase. `decisions` apply to the subject state after an
optional JSON round trip. `sibling_decisions` instead create and mutate a direct sibling and
therefore require `json_round_trip=False` and no subject decisions. Saved sibling fields are for a
single later resume. Duplicate call IDs and inconsistent combinations raise `ValueError`.
`StateProbe.name` must be a non-empty token without whitespace. Its zero-argument `capture`
callback runs before and after the phase, so keep it side-effect-free; `expected_after` is compared
with the second capture. An ordinary callback exception is recorded as a failed phase observation.

```text
ScenarioCase(
    agent: Agent[Any],
    input: str | list[TResponseInputItem],
    model: DeterministicModel,
    session: RecordingSession | None = None,
    run_config: RunConfig | None = None,
    max_turns: int | None = None,
    tool_counts: dict[str, int] = <new empty dict>,
    context: Any = None,
)

ScenarioPhase(
    phase_id: str,
    agent: Agent[Any],
    input: LiteralInput | ResumeInput,
    model: DeterministicModel,
    session: RecordingSession | None = None,
    run_config: RunConfig | None = None,
    max_turns: int | None = None,
    tool_counts: dict[str, int] = <new empty dict>,
    context: Any = None,
    expected_outcome: ExpectedOutcome = <new completed outcome>,
    expected_tool_counts_delta: Mapping[str, int] = <new empty dict>,
    probes: tuple[StateProbe, ...] = (),
    before: Callable[[], None | Awaitable[None]] | None = None,
    model_group: str = "default",
)

ScenarioPlan(phases: tuple[ScenarioPhase, ...])

Scenario(
    scenario_id: str,
    revision: int,
    description: str,
    variants: tuple[RunVariant, ...],
    invariants: tuple[str, ...],
    factory: Callable[[RunVariant], ScenarioCase | ScenarioPlan],
    expected_tool_counts: Mapping[str, int] = <new empty dict>,
    expected_outcome: ExpectedOutcome = <new completed outcome>,
    public_payloads: bool = False,
)
```

`ScenarioCase` is converted to a single phase. `ScenarioPhase` adds exact per-phase outcomes,
tool-count deltas, state probes, a sync or async `before` hook, and a model group. Phase IDs and
model-group names must be non-empty tokens; probe names must be unique and expected deltas must be
non-negative integers. Construction can raise `ValueError` for an invalid declaration.

`ScenarioPlan` requires unique ordered phases, backward-only resume references, consistent model
instances within each model group, and exactly one later consumption of every saved sibling.
`Scenario` requires a positive revision, unique non-empty variants and invariants, and the
`execution_outcome` invariant. Its factory is called once for every declared variant.

### Invariant names and observation requirements

These are the ten evaluator names accepted by the current runtime. An unknown name produces
`NOT_RUN` with `UNKNOWN_INVARIANT`; it is not treated as a passing check.

| Invariant | What it evaluates | Required scenario observations or declaration |
| --- | --- | --- |
| `execution_outcome` | Each phase completes, interrupts, or raises exactly as declared. | Required on every `Scenario`; `run_scenario()` supplies phase contracts and observations. |
| `stream_parity` | Normalized non-streaming and streaming results match. | Declare both `RunVariant.NON_STREAMING` and `RunVariant.STREAMING`; otherwise `NOT_RUN`. |
| `tool_linkage` | Function calls and outputs remain ordered, paired, unique, and compatible with pending interruptions across generated items, model inputs, and sessions. | Tool-linkage observations are captured for executed phases; use a function-tool path to exercise non-empty linkage. |
| `exactly_once` | Aggregate scenario-owned tool counts equal the declaration in each variant. | Set a non-empty `Scenario.expected_tool_counts`; otherwise `NOT_RUN`. The scenario's tool must update the shared `tool_counts` mapping. |
| `model_script_consumed` | The final phase for each model group leaves zero scripted steps. | Every case/phase already supplies a `DeterministicModel`; assign distinct `model_group` values to independent scripts. |
| `state_transitions` | Literal/resume kind, source, JSON restoration, interruption IDs, decisions, and saved-sibling metadata follow the phase contract. | Phase contracts are required. Use `ResumeInput` to observe resume/JSON/decision behavior; literal-only plans cover only literal transition state. |
| `state_fork_isolation` | Applying decisions to a direct sibling leaves the subject `RunState` byte-equivalent. | Include a direct `ResumeInput` with `sibling_decisions`; with no state fork the result is `NOT_RUN`. |
| `recursive_approval_routing` | One saved approved nested branch completes with its declared effect while the untouched branch remains interrupted. | Per variant, declare exactly one saved-sibling approval pattern: the fork saves one approved sibling with zero effects, then one later phase resumes it and expects completion plus a non-zero tool delta. |
| `phase_contract` | Observed phase ID/input kind/model group, tool-count delta, after-probe values, and the before-probe name set match the declaration. | Phase contracts are required; add `expected_tool_counts_delta` and side-effect-free `StateProbe` values for state owned by the scenario. |
| `session_replay` | Persisted session tool history appears in the first subsequent model input in order. | At least one phase needs non-empty prior session tool events and a model call; otherwise `NOT_RUN`. |

An evaluator may also return `NOT_RUN` when its required observations are absent. Certificate
validation recomputes these results, so a serialized `PASS` cannot substitute for missing evidence.

### Session double

```text
RecordingSession(
    session_id: str = "agentrunproof-session",
    items: list[TResponseInputItem] | None = None,
)

operations -> tuple[SessionOperation, ...]
async get_items(limit: int | None = None) -> list[TResponseInputItem]
async add_items(items: list[TResponseInputItem]) -> None
async pop_item() -> TResponseInputItem | None
async clear_session() -> None
snapshot() -> list[JsonValue]
```

The constructor deep-copies initial items. All reads return copies; mutations update only the
in-memory session and append an entry to `operations`. `snapshot()` returns a JSON-safe copy.
`SessionOperation` entries expose `operation`, `item_count`, and `limit`; that result type is not a
supported top-level import.

### Execution and certificate functions

```text
async run_scenario(scenario: Scenario) -> ProofRun
build_certificate(proof_run: ProofRun) -> dict[str, JsonValue]
validate_certificate(certificate: Any) -> dict[str, JsonValue]
load_certificate(path: Path) -> dict[str, JsonValue]
write_certificate(path: Path, certificate: dict[str, JsonValue]) -> None
```

`run_scenario()` calls the factory for every variant, executes each plan through the real Runner,
normalizes observations, evaluates requested invariants, and returns a new `ProofRun`. It disables
SDK tracing even if a supplied `RunConfig` enabled it. Factory and plan-construction errors
propagate. During phase execution, ordinary hook, tool, and Runner exceptions are captured as
observations and evaluated; `BaseException` subclasses and unexpected harness setup failures may
still propagate.

`ProofRun` is returned rather than imported. Its observable attributes are:

- `scenario: Scenario`;
- `observations: dict[RunVariant, Observation]`;
- `phase_contracts: dict[RunVariant, tuple[PhaseContract, ...]]`;
- `invariant_results: tuple[InvariantResult, ...]`;
- `status: str`, one of `PASS`, `FAIL`, or `NOT_RUN`.

`Observation`, `PhaseContract`, and `InvariantResult` are structured observable results but are not
supported top-level imports. Pass the entire `ProofRun` to `build_certificate()` instead of
constructing or importing its implementation types.

`build_certificate()` returns newly validated JSON-compatible data and can raise
`CertificateError` if serialization, redaction, provenance, or recomputed semantics are invalid.
`validate_certificate()` performs the same semantic checks on arbitrary input and returns a deep
copy. `load_certificate()` strictly reads UTF-8 JSON and then validates it. `write_certificate()`
validates first, creates parent directories, and atomically replaces the destination with sorted,
newline-terminated UTF-8 JSON; validation errors raise `CertificateError`, while reads and writes
can also raise `OSError`.

### Scenario building notes

- A `Scenario.factory` must return a fresh `ScenarioCase` or `ScenarioPlan` for each
  `RunVariant`. Do not reuse consumed model scripts across variants.
- Every scenario must request `execution_outcome`. Add only invariants whose observations the
  scenario actually provides; a missing observation is `NOT_RUN`, never `PASS`.
- `public_payloads=False` is the default. It replaces untrusted names and values with deterministic
  summaries before serialization. Those hashes can still be correlated or guessed for
  low-entropy data, so publish only reviewed synthetic evidence.
- `public_payloads=True` is appropriate only when all scenario inputs, tool payloads, outputs, and
  probe values are intentionally public and synthetic.
- `DeterministicModel(..., emit_traces=True)` can emit an ordinary SDK generation span when passed
  directly to the SDK Runner. `run_scenario()` always disables tracing for proof execution.

### Certificate interface

`build_certificate()` and `validate_certificate()` return JSON-compatible dictionaries. The
written JSON certificate—not `agentrunproof` CLI stdout—is the stable machine-readable artifact.
Its `certificate_id` is a SHA-256 content address over canonical JSON with the identifier field
cleared. Validation checks that address and recomputes the declared invariant results from the
normalized observations.

`write_certificate()` creates parent directories and replaces the destination atomically.
`load_certificate()` rejects duplicate keys and non-finite JSON numbers before semantic
validation. A certificate is an integrity record, not a signature or proof of execution; pair a
public claim with clean source provenance and visible CI or release evidence.

## `agentrunproof.current` advanced API

These functions enforce the exact schemas, environment relationships, paths, hashes, scenario
fingerprints, and limitations used by the repository's canonical current evidence. They are not
general-purpose bundle builders. A schema or evidence-case revision can require consumers to
update even when the ordinary scenario-building API is unchanged.

<!-- reference-table:agentrunproof.current:start -->
| Name | Kind | Purpose |
| --- | --- | --- |
| `BUNDLE_SCHEMA_VERSION` | Constant | Schema identifier for the canonical current counterexample bundle. |
| `CurrentBundleError` | Exception | Raised when current-case certificate or bundle evidence is malformed or semantically false. |
| `finalize_current_bundle` | Function | Fills a missing or null bundle ID with its content address, then validates the bundle and adjacent members. |
| `finalize_current_certificate` | Function | Copies a caller-supplied 40-character SHA into clean source metadata after validating only its format. |
| `load_current_bundle` | Function | Strictly reads a regular bundle file and validates it relative to its parent directory. |
| `parse_current_certificate_json` | Function | Strictly parses certificate JSON and enforces the exact current-case fingerprint. |
| `validate_current_bundle` | Function | Validates the bundle, source/runtime metadata, wheel/environment closure, and certificate member. |
| `validate_current_certificate` | Function | Validates certificate v1 plus the exact released sibling-isolation failure fingerprint. |
| `write_current_bundle` | Function | Validates and atomically writes canonical current-bundle JSON. |
| `COMPARISON_SCHEMA_VERSION` | Constant | Schema identifier for the released-versus-upstream comparison bundle. |
| `UpstreamComparisonError` | Exception | Raised when a comparison certificate or bundle is incomplete or inconsistent. |
| `finalize_comparison_certificate` | Function | Copies a caller-supplied 40-character SHA into clean source metadata after validating only its format. |
| `finalize_upstream_comparison` | Function | Fills a missing or null bundle ID with its content address, then validates the comparison and adjacent members. |
| `load_upstream_comparison` | Function | Strictly reads a regular comparison file and validates it relative to its parent directory. |
| `parse_worker_certificate_json` | Function | Strictly parses and validates certificate JSON intended as isolated comparison-worker output. |
| `validate_upstream_comparison` | Function | Validates comparison targets, wheel/source identity, environments, runs, fingerprints, and members. |
| `write_upstream_comparison` | Function | Validates and atomically writes canonical upstream-comparison JSON. |
<!-- reference-table:agentrunproof.current:end -->

### Current counterexample bundle calls

```text
BUNDLE_SCHEMA_VERSION: Final = "agentrunproof.current-bundle/v1"
class CurrentBundleError(ValueError)

finalize_current_certificate(
    value: Any,
    *,
    source_commit: str,
) -> dict[str, JsonValue]

parse_current_certificate_json(text: str) -> dict[str, JsonValue]
validate_current_certificate(value: Any) -> dict[str, JsonValue]

finalize_current_bundle(
    value: Any,
    *,
    directory: Path,
) -> dict[str, JsonValue]

validate_current_bundle(
    value: Any,
    *,
    directory: Path,
) -> dict[str, JsonValue]

load_current_bundle(path: Path) -> dict[str, JsonValue]
write_current_bundle(path: Path, value: Any) -> None
```

`validate_current_certificate()` requires certificate v1 plus the exact released
`runstate-sibling-approval-isolation` fingerprint and returns validated copied data.
`parse_current_certificate_json()` additionally rejects duplicate keys and non-finite numbers.
`finalize_current_certificate()` requires initially unavailable source metadata, validates that
`source_commit` looks like a 40-character Git object ID, writes that string plus clean-tree hashes
into a copy, recomputes its ID, and validates the result.

Critically, `finalize_current_certificate()` does not query Git, prove that the supplied object
exists, or check that a working tree is clean. The canonical generation script and release gates
must establish those facts before calling it.

`finalize_current_bundle()` requires a missing or null `bundle_id`, computes the content address,
and validates the bundle relative to `directory`; it does not write a file. Validation reads and
hashes required adjacent certificate data and, when present, the referenced wheel. The load
function requires a regular non-symlink bundle file and uses its parent as `directory`. The write
function validates relative to `path.parent` and then atomically replaces the bundle JSON file;
required adjacent members, and therefore their directory, must already exist. Contract failures
raise `CurrentBundleError`; filesystem failures can raise `OSError`.

### Released-versus-upstream comparison calls

```text
COMPARISON_SCHEMA_VERSION: Final = "agentrunproof.upstream-comparison/v1"
class UpstreamComparisonError(ValueError)

finalize_comparison_certificate(
    value: Any,
    *,
    source_commit: str,
) -> dict[str, JsonValue]

parse_worker_certificate_json(text: str) -> dict[str, JsonValue]

finalize_upstream_comparison(
    value: Any,
    *,
    directory: Path,
) -> dict[str, JsonValue]

validate_upstream_comparison(
    value: Any,
    *,
    directory: Path,
) -> dict[str, JsonValue]

load_upstream_comparison(path: Path) -> dict[str, JsonValue]
write_upstream_comparison(path: Path, value: Any) -> None
```

`parse_worker_certificate_json()` strictly parses ordinary certificate JSON and wraps certificate
errors as `UpstreamComparisonError`. `finalize_comparison_certificate()` requires unavailable
source metadata, checks only that `source_commit` has the 40-character Git-object form, copies it
with clean-tree hashes into a new certificate, recomputes the content ID, and validates it. It does
not inspect a repository, establish that the SHA exists, or prove cleanliness; the canonical
comparison script and release gates supply that provenance evidence separately.

`finalize_upstream_comparison()` requires a missing or null `bundle_id`, computes it, and validates
without writing. Validation enforces target identities, wheel and source hashes, environment
closures, run order, exact evidence fingerprints, and adjacent members under `directory`.
`load_upstream_comparison()` rejects symlinks/non-files and validates relative to the path's
parent. `write_upstream_comparison()` validates relative to `path.parent`, creates parent
directory only after validation, and atomically replaces the JSON file; required adjacent members
must already exist. Contract failures raise `UpstreamComparisonError`; filesystem failures can
raise `OSError`.

All `directory` parameters identify the directory containing referenced bundle members. Loaders
derive it from the bundle path. Writers validate against `path.parent`; required member files must
already exist. Do not construct or patch canonical evidence by hand—use the repository generation
scripts and clean-source release gates.
