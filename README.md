# AgentRunProof

[![CI](https://github.com/FU-max-boop/agentrunproof/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/FU-max-boop/agentrunproof/actions/workflows/ci.yml?query=branch%3Amain)
[![PyPI](https://img.shields.io/pypi/v/agentrunproof.svg)](https://pypi.org/project/agentrunproof/)
[![Python](https://img.shields.io/pypi/pyversions/agentrunproof.svg)](https://pypi.org/project/agentrunproof/)
[![License: MIT](https://img.shields.io/github/license/FU-max-boop/agentrunproof.svg)](https://github.com/FU-max-boop/agentrunproof/blob/main/LICENSE)

![AgentRunProof: runtime bugs deserve proofs, not screenshots](https://raw.githubusercontent.com/FU-max-boop/agentrunproof/main/docs/assets/social-preview.png)

**Catch OpenAI Agents SDK `Runner` regressions without an API key.**

AgentRunProof runs deterministic scenarios against the real `Runner`, compares observable behavior
across `run` and `run_streamed`, and writes content-addressed JSON records for stream, session,
tool-linkage, and `RunState` resume invariants. A failing record carries the normalized
counterexample observations.

The released 0.3.0 contract declares `openai-agents>=0.20,<0.23` on Python 3.10–3.14. Its
packaged-wheel CI matrix requires the exact 0.20.0, 0.21.0, and 0.22.0 release baselines. Published
availability is shown by the PyPI badge and immutable GitHub Releases. Built-in scenarios make no
model API call and require no API key.

> AgentRunProof-backed reports are referenced by two merged maintainer fixes,
> [#4413](https://github.com/openai/openai-agents-python/pull/4413) and
> [#4414](https://github.com/openai/openai-agents-python/pull/4414). This is upstream diagnostic
> impact—not OpenAI adoption, dependency, or endorsement.

Read the five-minute
[RunState case study](https://github.com/FU-max-boop/agentrunproof/blob/main/docs/case-study-runstate.md)
for the released failure, the recursive follow-up, and the exact before/after evidence chain.
For complete usage details, start with the
[documentation index](https://github.com/FU-max-boop/agentrunproof/blob/main/docs/README.md),
[Python API reference](https://github.com/FU-max-boop/agentrunproof/blob/main/docs/api-reference.md),
or [CLI reference](https://github.com/FU-max-boop/agentrunproof/blob/main/docs/cli-reference.md).

## 30-second local check

```bash
python -m pip install agentrunproof
agentrunproof probe basic-tool-session-parity --certificate proof.json
agentrunproof check-certificate proof.json
```

Expected output:

```text
PASS basic-tool-session-parity
  PASS    execution_outcome: OK
  PASS    stream_parity: OK
  PASS    tool_linkage: OK
  PASS    exactly_once: OK
  PASS    model_script_consumed: OK
certificate_id: sha256:...
written: proof.json
VALID sha256:... PASS
```

Exit `0` means PASS, `1` means an observed invariant violation, and `2` means invalid or
unverifiable evidence. See the
[provider-free real Runner example](https://github.com/FU-max-boop/agentrunproof/blob/main/examples/provider_free_tool_demo.py)
and the
[OpenAI Agents integration guide](https://github.com/FU-max-boop/agentrunproof/blob/main/docs/openai-agents.md).

## Paid design-partner pilot

AgentRunProof is testing a small managed service for teams that depend on a private standard-text
OpenAI Agents `Runner` workflow: a 7-day, fixed-scope compatibility check in customer-owned GitHub
Actions. It maps one sanitized workflow to an already-supported deterministic contract, exercises
two different preverified SDK baselines from 0.20.0, 0.21.0, and 0.22.0, and delivers copyable CI
commands plus a short result report. It does not include bespoke adapters, a defect fix, or an
open-ended regression investigation.

The founding-pilot price is **USD 20 paid before work begins** for up to 90 minutes of engineering,
with no automatic renewal and three slots available. The default delivery uses only public or
customer-approved synthetic material and does not require private repository access. Private
repository content, production prompts, API keys, raw customer data, and private certificates are
not shared with the maintainer or external AI tools. This is a low-friction paid-demand experiment,
not proof of sustainable pricing, a hosted AgentRunProof feature, an uptime SLA, a security audit,
or a guarantee that every SDK defect will be found.

Read the exact
[scope, deliverables, and data boundary](https://github.com/FU-max-boop/agentrunproof/blob/main/docs/design-partner-pilot.md),
then use the
[design-partner intake](https://github.com/FU-max-boop/agentrunproof/issues/new?template=design-partner-pilot.yml)
if the fixed pilot fits. Do not put private code, credentials, prompts, or production traces in the
public issue.

Maintaining a downstream library? The
[isolated, test-only CI guide](https://github.com/FU-max-boop/agentrunproof/blob/main/docs/ci-adoption.md)
provides a copyable real-`Runner` contract test and an ephemeral `uv` matrix. The published
[v0.3.0 release](https://github.com/FU-max-boop/agentrunproof/releases/tag/v0.3.0) and
[PyPI package](https://pypi.org/project/agentrunproof/0.3.0/) cover exact packaged baselines for SDK
0.20.0, 0.21.0, and 0.22.0. The isolated pattern keeps AgentRunProof out of runtime metadata and
the project lockfile.

For artifact review, pin an exact published version and use its matching
[immutable GitHub Release](https://github.com/FU-max-boop/agentrunproof/releases). Each
`SHA256SUMS` binds the wheel and sdist. The release workflow rebuilds and byte-compares those
artifacts, smoke-tests the wheel, and publishes the same verified files to PyPI through OIDC trusted
publishing. The CI guide explains the remaining third-party-code trust boundary.

## Where it fits

Use the SDK's public `agents.testing.ScriptedModel` with `pytest` for a focused deterministic
application or SDK test. AgentRunProof delegates to `ScriptedModel` on supported SDKs 0.21 and
0.22 and adds reusable scenario orchestration, automatic `run`/`run_streamed` comparison,
multi-phase `RunState` checks, cross-version evidence, and content-addressed records.

| You need to… | Start with |
| --- | --- |
| Script model responses and assert one application behavior | `agents.testing.ScriptedModel` + `pytest` |
| Compare the same contract across runner modes or SDK versions | AgentRunProof |
| Check approval/rejection and JSON-restored `RunState` flows | AgentRunProof |
| Share a normalized record that can be checked without a provider call | AgentRunProof |
| Evaluate model-output quality | An eval framework, not AgentRunProof |

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
- output-guardrail tool-pair durability, including SDK 0.22's removal of a rejected raw tool result
  from durable replay without depending on its replacement wording.

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
and the final recursive and serialized PASS results by wheel hash and Git provenance. The v0.2.0
release carries a freshly reproduced bundle for the same causal ladder, bound to the v0.2.0
harness wheel.

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

## Contribute a runtime contract

Found a public-API `Runner` inconsistency?
[Open a scenario request](https://github.com/FU-max-boop/agentrunproof/issues/new/choose) with the
exact SDK version and a minimal reproducer. Want to make it permanent? See the
[contribution guide](https://github.com/FU-max-boop/agentrunproof/blob/main/CONTRIBUTING.md) and add
the smallest failing scenario. For usage questions and early contract ideas, use
[Discussions](https://github.com/FU-max-boop/agentrunproof/discussions).

If AgentRunProof belongs in your regression toolbox, star the repository so other SDK maintainers
can find it.

## License

MIT
