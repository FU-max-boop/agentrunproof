# AgentRunProof project charter

## Mission

AgentRunProof is a deterministic runtime-conformance harness for `openai-agents-python`. It executes equivalent scripted workflows across runtime paths, checks state and side-effect invariants, and emits integrity-bound records whose normalized observations can be independently rechecked without a model API call.

The project exists to make cross-path runtime regressions cheap to reproduce and difficult to dismiss. Its primary users are Agents SDK contributors, regression-fixture authors, and application teams whose workflows depend on interruption, resume, guardrail, or streaming semantics.

## Runtime contract

The first public contract covers the standard text `Runner` and these released public boundaries:

- `Model`, normalized `ModelResponse`, and a terminal-event streaming profile;
- `Runner.run()` and `Runner.run_streamed()`;
- `RecordingSession` and its observable persisted items and operations;
- selected serializable `RunState` interruption, exact decision, and resume transitions;
- one selected sibling-`RunState` approval-isolation scenario using repeated public
  `RunResult.to_state()` calls;
- one selected two-edge `Agent.as_tool` approval-routing scenario that resumes one decided direct
  sibling while leaving the untouched sibling interrupted;
- one selected recursive `Agent.as_tool` approval-routing scenario that crosses the durable
  `RunState` JSON boundary and applies one exact approval after restoration;
- function-tool calls and model-visible function-tool contracts;
- one pinned output-guardrail durability scenario expressed through public APIs.

For one logical scenario, AgentRunProof may execute several variants. A variant is a real SDK run, not a simulation of the runner. The harness controls only model responses, local test tools, and the test session.

The v0.3 release candidate declares the compatibility window `openai-agents>=0.20,<0.23` on
Python 3.10 through 3.14. Its acceptance matrix installs the packaged artifact against exactly SDK
0.20.0, 0.21.0, and 0.22.0; a Python/SDK cell is claimed as tested only after that exact CI cell
passes. The latest published release remains v0.2.0 until the v0.3 release gates complete.

v0.3 retains certificate v1, the v0.1 runtime scenarios, and v0.2's explicit
`DeterministicModel(..., emit_traces=True)` interoperability path: supported SDKs 0.21 and 0.22
delegate to the public `agents.testing.ScriptedModel`, while SDK 0.20 uses the existing
public-`Model` fallback. The option emits an ordinary SDK generation span only when the caller runs
the model directly through `Runner`; `run_scenario()` continues to disable tracing. AgentRunProof
does not evaluate span payloads or act as a tracing backend.

SDK 0.22 deliberately replaces a terminal tool result rejected by an output guardrail before
durable session replay. The selected guardrail-history scenario therefore requires the linked
call/output pair to remain present while the rejected raw tool result is absent. It does not bind
the SDK's human-readable replacement text. SDK 0.20 and 0.21 retain the historical raw-output
expectation used by the pinned 0.19.x regression boundary.

The terminal-event profile emits `response.output_item.done` and `response.completed`; it does not cover token/delta timing, backpressure, or stream cancellation. Generic handoff execution, retries, cancellation, max-turn cleanup, generalized object snapshot isolation, and arbitrary guardrail contracts remain planned scenarios rather than v0.3 runtime claims.

## Required invariants

The certificate-v1 engine can report these invariant families:

1. **Execution outcome:** each phase declares whether it must complete, interrupt a precise number of times, or raise a qualified Runner exception; transition and observation errors never satisfy a Runner-exception contract.
2. **Terminal-event stream parity:** equivalent scripted behavior produces equivalent post-run normalized observations in streaming and non-streaming runs, excluding event-type differences by design.
3. **Tool linkage:** ordered invocation/output identities are checked separately in generated items, before/after session snapshots, and every model input; only the exact pending interruption may lack an output.
4. **Exactly-once side effects:** a scenario declares expected counts for its local tool invocations, globally and per phase.
5. **Model-script consumption:** the final phase using each scripted model group must consume every declared response.
6. **State transitions:** a resume may bind canonical JSON transport, public `RunState.from_json()` reconstruction, restored-state equality, interruption identities, and exact approve/reject decisions.
7. **State-fork isolation:** a decision applied to one direct sibling state must not mutate another
   state returned from the same paused result.
8. **Recursive approval routing:** a saved or JSON-restored approved state must resume through its
   declared nested agent-tool checkpoints, complete without another interruption, and commit its
   expected effect.
9. **Phase contract:** observed tool-count deltas and scenario probes must match the declared value after every phase.
10. **Session replay:** persisted tool events observed before a phase must appear in the first model input when that comparison is available.

An invariant is evaluated only when the scenario declares the observations required to judge it. Unsupported or unobserved conditions produce `NOT_RUN`, never an inferred pass.

## Evidence contract

Each certificate is canonical JSON with:

- schema version and certificate identifier;
- AgentRunProof version and clean source provenance when available;
- Python, platform, `openai-agents`, `openai`, and Pydantic versions;
- scenario identifier, revision, requested invariants, expected outcomes, phase contracts, and normalized model-boundary input digest;
- exact runtime variants and their normalized observations;
- invariant results with machine-readable reasons;
- redaction summary and explicit limitations.

The certificate identifier is the SHA-256 digest of the canonical certificate payload with the identifier field omitted. A certificate is an integrity record, not a signature or proof that an untrusted publisher executed the stated commands. Public evidence must additionally be pinned by a clean Git commit and CI or release artifact.

The certificate-v1 private profile is designed to prevent raw payload serialization, not to provide cryptographic confidentiality. Its deterministic unsalted hashes expose equality and can be dictionary-guessed for low-entropy values; private certificates remain local unless every field has been reviewed for publication.

## Deliberate exclusions

The current runtime contract does not provide:

- model-output quality evaluation or an LLM judge;
- a tracing, observability, or hosted dashboard backend;
- HTTP-level record/replay;
- production interception of arbitrary tool side effects;
- drop-in conformance testing for arbitrary custom `Session` implementations;
- a sandbox for user-defined scenario tools, hooks, or probes;
- token/delta, timing, backpressure, or cancellation-stream conformance;
- Realtime, Voice, Sandbox, or MCP wire-protocol conformance;
- multi-framework support;
- a claim that all constructible SDK states are supported behavior.

A sanitized model-boundary cassette recorder remains a possible later module. Realtime and
Agents-specific MCP lifecycle adapters remain deferred until the text-runner contract has broader
external use.

## Adoption strategy

AgentRunProof earns an upstream integration by evidence rather than by requesting a default dependency.

1. Reproduce at least three historical regressions across known buggy and fixed revisions.
2. Find and report at least one current upstream defect with a minimal certificate and ordinary pytest reproducer.
3. Obtain an external run or contributor confirmation.
4. Propose the smallest useful upstream surface: a referenced regression fixture, an optional development/nightly conformance job, or a documentation link.

Official runtime dependency status is not a project goal. A public upstream citation, accepted reproducer, test reuse, documentation reference, or maintainer acknowledgement satisfies the external-recognition gate.

That gate was first met on 2026-08-14: an OpenAI Agents maintainer acknowledged the reported
checkpoint-isolation defect and merged PR #4413 with an explicit reference to the report. The
result recognizes AgentRunProof's diagnostic evidence; it does not imply an official SDK
dependency or blanket endorsement.

The subsequent recursive and serialized cases were used to validate upstream PR #4414 before its
merge as `50d65f65`. A
community-tool entry was proposed on PR #4381; the maintainer declined the listing to keep that
guide limited to SDK-maintained APIs while explicitly welcoming future findings backed by
AgentRunProof. The project therefore remains external to the SDK.

## Release gates

- **Gate 0 — falsifiable prototype:** three historical buggy/fixed pairs are distinguished deterministically using only released public runtime interfaces or a clearly isolated version adapter.
- **Gate 1 — reviewable package:** unit, parameterized, schema, tamper, type, formatting, and package checks pass; the built wheel is installed and tested on every supported Python version, and the sdist passes a separate fresh-environment smoke test.
- **Gate 2 — public evidence:** canonical historical certificates are generated from a clean commit and verified independently in CI.
- **Gate 3 — external defect:** a current external failure is reproduced and reported accurately without overstating impact.
- **Gate 4 — recognition:** an upstream maintainer or independent adopter cites, runs, reuses, documents, or substantively responds to AgentRunProof evidence.

PyPI publication requires Gates 0 through 2. Each compatibility-window release requires a fresh
versioned comparison bundle bound to that release's clean source and wheel; an older release's
immutable evidence cannot satisfy this gate. The long-term project goal completes after Gate 4.
