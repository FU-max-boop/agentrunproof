# AgentRunProof

AgentRunProof is a deterministic runtime-conformance harness for the OpenAI Agents SDK. It drives the real `Runner` with scripted public-`Model` responses, compares observable state across execution paths, and writes a content-addressed conformance record. A failing record carries the normalized counterexample observations.

AgentRunProof v0.2 declares the `openai-agents>=0.20,<0.22` compatibility window on Python
3.10–3.14. Its packaged CI matrix verifies the exact 0.20.0 and 0.21.0 release baselines.
Built-in scenarios make no model API call and require no API key.

## What AgentRunProof checks

- declared completion, interruption, or Runner-exception outcomes for every scenario phase;
- post-run parity between non-streaming execution and scripted terminal-event streaming (`response.output_item.done` plus `response.completed`);
- ordered function-call/output linkage in generated items, `Session` snapshots, and every model input;
- declared counts for scenario-owned local tool invocations;
- consumption of each deterministic model script;
- selected public `RunState` transitions: JSON transport, `from_json()` reconstruction, restored-state equality, interruption identities, and exact approve/reject decisions;
- direct sibling-`RunState` approval isolation from repeated `RunResult.to_state()` calls;
- recursive approval routing through two `Agent.as_tool` checkpoints while preserving an untouched
  direct sibling state;
- recursive approval routing after a public `RunState.to_json()` / `RunState.from_json()` boundary,
  with one exact approval applied to the restored interruption;
- per-phase tool-count deltas, scenario probes, and replay of persisted tool history.

The terminal-event profile does not claim token/delta, timing, backpressure, or cancellation-stream equivalence. Generic handoff, retry, cancellation, max-turn, generalized snapshot-isolation, and task-cleanup contracts remain future scenarios unless a certificate explicitly names and observes them.

AgentRunProof checks SDK runtime semantics. It is not a model-quality evaluator, tracing backend,
HTTP recorder, hosted service, or general agent framework.

For observability integration tests, `DeterministicModel(..., emit_traces=True)` emits the SDK's
ordinary generation span while the real `Runner` emits its agent and tool spans. The default is
`False`, and `run_scenario()` still disables tracing. Built-ins remain provider-free, but arbitrary
scenario tools and hooks are not network-sandboxed. Use the opt-in by passing the model directly
to `Runner.run()` or `Runner.run_streamed()`; any installed trace processor may export data or make
network requests.

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

The recursive routing probe exercises the remaining boundary after upstream #4413:

```bash
agentrunproof probe runstate-recursive-agent-tool-approval-routing \
  --certificate build/runstate-recursive-approval.json
agentrunproof check-certificate build/runstate-recursive-approval.json
```

It pauses a protected effect behind two `Agent.as_tool` edges, creates two direct sibling states,
approves only one flattened interruption, and resumes both branches. On upstream commit `0b93ce8`,
the untouched sibling correctly remains pending but the approved sibling also remains interrupted;
the focused result is
`recursive_approval_routing: APPROVED_NESTED_STATE_REMAINED_INTERRUPTED`. A corrected runtime must
finish the approved branch with exactly one effect in both runner modes while leaving the untouched
branch at zero effects.

The serialized-routing probe checks the durable form of the same contract:

```bash
agentrunproof probe runstate-recursive-agent-tool-approval-serialization \
  --certificate build/runstate-recursive-approval-serialization.json
agentrunproof check-certificate build/runstate-recursive-approval-serialization.json
```

The initial head of upstream PR #4414 (`9dc7da9`) fixed the live path but remained interrupted after
JSON restoration. The revised head `1725a898` passes the built-in restored-approval scenario in both
runner modes and was squash-merged as `50d65f65`; the upstream 24-case regression also covers
approval and rejection before and after restoration across two and three nested edges. The
immutable v0.1.2 comparison bundle pins the released failure, the intermediate merged behavior,
and the final recursive and serialized PASS results by wheel hash and Git provenance.

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

The exact release gates and exclusions are in the [project charter](https://github.com/FU-max-boop/agentrunproof/blob/main/PROJECT_CHARTER.md). The [execution plan](https://github.com/FU-max-boop/agentrunproof/blob/main/PLAN.md) tracks releases, evidence, and external-adoption work.

AgentRunProof received its first maintainer-level citation when OpenAI Agents follow-up
[#4413](https://github.com/openai/openai-agents-python/pull/4413) cited the reported checkpoint
isolation defect. The next adoption target is reuse of the recursive regression fixture, an
optional CI check, or a documentation reference—not a default SDK dependency. A community-tool
entry was [proposed on the official v0.21 testing-guide PR](https://github.com/openai/openai-agents-python/pull/4381#issuecomment-5293600461). The maintainer [kept that guide limited to SDK-maintained APIs](https://github.com/openai/openai-agents-python/pull/4381#issuecomment-5293704972) while explicitly welcoming future reproducible findings backed by the tool. AgentRunProof therefore remains an external project rather than an official SDK listing or dependency.

## License

MIT
