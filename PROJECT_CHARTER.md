# AgentRunProof project charter

## Mission

AgentRunProof is a deterministic runtime-conformance harness for `openai-agents-python`. It executes equivalent scripted workflows across runtime paths, checks state and side-effect invariants, and emits integrity-bound records whose normalized observations can be independently rechecked without a model API call.

The project exists to make cross-path runtime regressions cheap to reproduce and difficult to dismiss. Its primary users are Agents SDK contributors, regression-fixture authors, and application teams whose workflows depend on interruption, resume, guardrail, or streaming semantics.

## v0.1 contract

The first public contract covers the standard text `Runner` and these released public boundaries:

- `Model`, normalized `ModelResponse`, and a terminal-event streaming profile;
- `Runner.run()` and `Runner.run_streamed()`;
- `RecordingSession` and its observable persisted items and operations;
- selected serializable `RunState` interruption, exact decision, and resume transitions;
- function-tool calls and model-visible function-tool contracts;
- one pinned output-guardrail durability scenario expressed through public APIs.

For one logical scenario, AgentRunProof may execute several variants. A variant is a real SDK run, not a simulation of the runner. The harness controls only model responses, local test tools, and the test session.

The target compatibility baseline is `openai-agents` 0.20.x on Python 3.10 through 3.14. Support for a Python/SDK pair is claimed only after the packaged artifact passes that exact CI cell.

The terminal-event profile emits `response.output_item.done` and `response.completed`; it does not cover token/delta timing, backpressure, or stream cancellation. Generic handoff execution, retries, cancellation, max-turn cleanup, snapshot isolation, and arbitrary guardrail contracts remain planned scenarios rather than v0.1 claims.

## Required invariants

The v0.1 engine can report these invariant families:

1. **Execution outcome:** each phase declares whether it must complete, interrupt a precise number of times, or raise a qualified Runner exception; transition and observation errors never satisfy a Runner-exception contract.
2. **Terminal-event stream parity:** equivalent scripted behavior produces equivalent post-run normalized observations in streaming and non-streaming runs, excluding event-type differences by design.
3. **Tool linkage:** ordered invocation/output identities are checked separately in generated items, before/after session snapshots, and every model input; only the exact pending interruption may lack an output.
4. **Exactly-once side effects:** a scenario declares expected counts for its local tool invocations, globally and per phase.
5. **Model-script consumption:** the final phase using each scripted model group must consume every declared response.
6. **State transitions:** a resume may bind canonical JSON transport, public `RunState.from_json()` reconstruction, restored-state equality, interruption identities, and exact approve/reject decisions.
7. **Phase contract:** observed tool-count deltas and scenario probes must match the declared value after every phase.
8. **Session replay:** persisted tool events observed before a phase must appear in the first model input when that comparison is available.

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

The v0.1 private profile is designed to prevent raw payload serialization, not to provide cryptographic confidentiality. Its deterministic unsalted hashes expose equality and can be dictionary-guessed for low-entropy values; private certificates remain local unless every field has been reviewed for publication.

## Deliberate exclusions

v0.1 does not provide:

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

A sanitized model-boundary cassette recorder is a candidate for v0.2. Realtime and Agents-specific MCP lifecycle adapters are later modules only after the text-runner contract has external evidence.

## Adoption strategy

AgentRunProof earns an upstream integration by evidence rather than by requesting a default dependency.

1. Reproduce at least three historical regressions across known buggy and fixed revisions.
2. Find and report at least one current upstream defect with a minimal certificate and ordinary pytest reproducer.
3. Obtain an external run or contributor confirmation.
4. Propose the smallest useful upstream surface: a referenced regression fixture, an optional development/nightly conformance job, or a documentation link.

Official runtime dependency status is not a v0.1 goal. A public upstream citation, accepted reproducer, test reuse, documentation reference, or maintainer acknowledgement satisfies the external-recognition gate.

## Release gates

- **Gate 0 — falsifiable prototype:** three historical buggy/fixed pairs are distinguished deterministically using only released public runtime interfaces or a clearly isolated version adapter.
- **Gate 1 — reviewable package:** unit, parameterized, schema, tamper, type, formatting, and package checks pass; the built wheel is installed and tested on every supported Python version, and the sdist passes a separate fresh-environment smoke test.
- **Gate 2 — public evidence:** canonical historical certificates are generated from a clean commit and verified independently in CI.
- **Gate 3 — external defect:** a current external failure is reproduced and reported accurately without overstating impact.
- **Gate 4 — recognition:** an upstream maintainer or independent adopter cites, runs, reuses, documents, or substantively responds to AgentRunProof evidence.

PyPI publication requires Gates 0 through 2. The long-term project goal completes after Gate 4.
