# AgentRunProof

AgentRunProof is a deterministic runtime-conformance harness for the OpenAI Agents SDK. It drives the real `Runner` with scripted public-`Model` responses, compares observable state across execution paths, and writes a content-addressed conformance record. A failing record carries the normalized counterexample observations.

AgentRunProof v0.1 targets `openai-agents` 0.20.x on Python 3.10–3.14. Built-in scenarios make no model API call and require no API key.

## What v0.1 checks

- declared completion, interruption, or Runner-exception outcomes for every scenario phase;
- post-run parity between non-streaming execution and scripted terminal-event streaming (`response.output_item.done` plus `response.completed`);
- ordered function-call/output linkage in generated items, `Session` snapshots, and every model input;
- declared counts for scenario-owned local tool invocations;
- consumption of each deterministic model script;
- selected public `RunState` transitions: JSON transport, `from_json()` reconstruction, restored-state equality, interruption identities, and exact approve/reject decisions;
- direct sibling-`RunState` approval isolation from repeated `RunResult.to_state()` calls;
- per-phase tool-count deltas, scenario probes, and replay of persisted tool history.

The terminal-event profile does not claim token/delta, timing, backpressure, or cancellation-stream equivalence. Generic handoff, retry, cancellation, max-turn, generalized snapshot-isolation, and task-cleanup contracts remain future scenarios unless a certificate explicitly names and observes them.

AgentRunProof checks SDK runtime semantics. It is not a model-quality evaluator, tracing backend, HTTP recorder, hosted service, or general agent framework.

## Development quickstart

```bash
python -m pip install -e ".[test,dev]"
agentrunproof --version
agentrunproof list-scenarios
agentrunproof probe basic-tool-session-parity --certificate build/basic.json
agentrunproof check-certificate build/basic.json
```

A successful probe exits `0`; an observed invariant violation exits `1`; invalid input or unverifiable evidence exits `2`.

The sibling-isolation probe intentionally exposes a released SDK counterexample:

```bash
agentrunproof probe runstate-sibling-approval-isolation \
  --certificate build/runstate-sibling-isolation.json
agentrunproof check-certificate build/runstate-sibling-isolation.json
```

On `openai-agents==0.20.0`, approving one sibling state also mutates an untouched sibling; resuming
that untouched state executes the protected tool. The certificate records
`state_fork_isolation: SIBLING_STATE_MUTATED` and the associated unexpected outcome and side
effect. This adjacent gap was reported on upstream PR
[#4409](https://github.com/openai/openai-agents-python/pull/4409#issuecomment-5291724795);
the report is not a claim that #4409 introduced the bug.

For library scenarios, the top-level package exposes `Scenario`/`ScenarioCase` for one run and `ScenarioPlan`/`ScenarioPhase`/`ResumeInput`/`StateProbe` for ordered multi-run contracts, together with `DeterministicModel`, `RecordingSession`, `run_scenario()`, and certificate helpers. The built-in scenario and the two multi-phase historical scenarios are executable examples.

## Historical falsification matrix

The development matrix uses only released SDK wheels and public runtime interfaces:

| Upstream case | Buggy boundary | Fixed boundary | Required fingerprint |
| --- | --- | --- | --- |
| [#4322](https://github.com/openai/openai-agents-python/issues/4322) | 0.19.4 FAIL | 0.20.0 PASS | session limiting must not send an orphan function output to the model |
| [#4244](https://github.com/openai/openai-agents-python/issues/4244) | 0.19.4 FAIL | 0.20.0 PASS | serialized approval must survive a context-overridden resume and execute once |
| [#4125](https://github.com/openai/openai-agents-python/issues/4125) | 0.19.2 streamed FAIL | 0.19.3 PASS | a committed tool call/output pair must survive a resumed output-guardrail tripwire |

Run a local, non-canonical rehearsal with:

```bash
python scripts/run_history_matrix.py --output-directory build/history-rehearsal
agentrunproof check-history-matrix build/history-rehearsal/matrix.json
```

Canonical evidence is stricter: Linux x86_64 CPython 3.12, fresh environments, hash-locked wheel closures, isolated worker processes, a Python socket-deny guard during scenario execution, an exact clean Git commit, and a bundle marker written last. Artifact acquisition occurs before the network guard and is explicitly recorded as a limitation. The immutable v0.1.0 Gate 2 bundle is published under [`evidence/history/v1`](https://github.com/FU-max-boop/agentrunproof/tree/main/evidence/history/v1).

The 0.19.x rows are historical-only compatibility probes, not supported installations: the harness wheel is installed with `--no-deps` over each locked legacy SDK closure, and that dependency-metadata bypass is explicit in the canonical bundle.

## Evidence and trust boundary

Certificate and history identifiers are SHA-256 addresses over canonical JSON. The independent checker rejects schema drift, non-finite or duplicate-key JSON, semantic inconsistencies, forged phase transitions, altered historical fingerprints, a missing or tampered required matrix/marker, and internally inconsistent source-state metadata. The referenced wheel is optional beside a local marker and is separately bound by CI or release artifacts.

Checking a record re-evaluates its normalized observations; it does not rerun the SDK, authenticate an untrusted publisher, or prove that the stated command executed. Public claims therefore require the clean source commit plus a visible CI or release anchor.

The private profile prevents raw observed payloads from being serialized, but values still exist in the scenario process. Its deterministic unsalted hashes are correlatable and may be dictionary-guessed for low-entropy values. Arbitrary user-defined tools, hooks, and probes are not sandboxed. Treat private records as local diagnostics and publish only reviewed synthetic evidence.

## Project contract

The exact release gates and exclusions are in the [project charter](https://github.com/FU-max-boop/agentrunproof/blob/main/PROJECT_CHARTER.md). The [execution plan](https://github.com/FU-max-boop/agentrunproof/blob/main/PLAN.md) tracks the completed historical release and the current upstream counterexample.

AgentRunProof is seeking evidence-backed adoption: a conventional upstream reproducer, optional CI fixture, or documentation reference—not a default SDK dependency.

## License

MIT
