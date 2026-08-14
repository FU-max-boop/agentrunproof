# Build AgentRunProof from prototype to external recognition

This is a living execution plan. Keep `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` current as implementation and external evidence evolve.

## Purpose

Deliver a standalone, installable tool that deterministically checks OpenAI Agents SDK runtime state semantics, produces independently recheckable conformance records, demonstrates value on historical regressions, and earns a public upstream or adopter acknowledgement.

## Progress

- [x] (2026-08-14) Audited the upstream issue/PR landscape and selected Runner state consistency as the project boundary.
- [x] (2026-08-14) Confirmed official OpenAI documentation covers runtime state, traces, and evals but not deterministic state conformance.
- [x] (2026-08-14) Confirmed upstream `main` added public scripted testing utilities in PR #4362 after the v0.20.0 release.
- [x] (2026-08-14) Reserved the working project/package name `AgentRunProof` / `agentrunproof` locally; exact GitHub and PyPI names were unoccupied at the check time.
- [x] (2026-08-14) Implemented the release-candidate executable scenario, phase,
  invariant-result, and certificate v1 contracts; freeze begins with the first source commit.
- [x] (2026-08-14) Implemented the public-`Model` deterministic harness,
  `RecordingSession`, real streaming/non-streaming Runner execution, multi-phase `RunState`
  resume, strict content-addressed checking, and a built-in scenario requiring no API key.
- [x] (2026-08-14) Reproduced issue #4322 across released wheels: v0.19.4 fails by
  sending an orphan tool output to the model after session limiting, while v0.20.0 passes.
- [x] (2026-08-14) Reproduced issue #4244 across released wheels: v0.19.4 loses a
  serialized approval when resume supplies a context override, while v0.20.0 executes it once.
- [x] (2026-08-14) Reproduced issue #4125 across released wheels: v0.19.2 loses the
  completed tool output only on streamed resume after an output guardrail tripwire, while
  v0.19.3 preserves and replays the atomic call/output pair.
- [x] (2026-08-14) Completed the Gate 0 development rehearsal: all six fresh-environment
  buggy/fixed runs matched their exact semantic fingerprints using real Runner execution and
  released public interfaces. Clean Linux canonical evidence remains Gate 2.
- [x] (2026-08-14) Completed the adversarial, package, and public-evidence release gates.
- [x] (2026-08-14) Published the public repository and immutable `v0.1.0` GitHub
  release after all Python 3.10-3.14 package cells and the Linux evidence job passed.
  PyPI publication is awaiting account verification and OIDC trust setup.
- [ ] Report the current `RunResult.to_state()` sibling-approval isolation counterexample with
  a conventional upstream regression test and an AgentRunProof certificate.
- [ ] Obtain external recognition.

## Milestones

### Milestone 1: falsifiable engine

Create a small Python package with a real `Model` test double, recording `Session`, normalized observations, stream/non-stream execution, invariant evaluation, canonical certificate generation, validation, and a CLI. Add one simple passing scenario and one intentionally inconsistent fixture so fail-closed behavior is testable before historical archaeology is complete.

The milestone is complete when `python -m agentrunproof probe` runs without network access, produces the same certificate on repeated runs apart from explicitly excluded timestamps, and exits nonzero for a violated invariant.

### Milestone 2: historical evidence matrix

For at least three upstream defects, pin the issue, buggy revision, fix revision, version environment, and scenario. Run each scenario against both sides in isolated environments. Store one strict matrix plus its final bundle marker, not upstream source or third-party distributions.

The milestone is complete when every buggy revision fails the intended invariant, every paired fixed revision passes, and an isolated checker rejects tampered evidence.

### Milestone 3: public release candidate

Harden the public contract, documentation, redaction behavior, schema, package metadata, CI matrix, and supply-chain boundaries. Run independent correctness, evidence, security/privacy, and adoption-fit reviews. Build wheel and sdist, install both in fresh environments, and verify exact artifact contents.

The milestone is complete when Gate 1 passes on source commit C and Gate 2 passes on an evidence-only child commit E whose bundle binds C. This two-commit design avoids making evidence claim provenance from a commit that already contains itself.

### Milestone 4: publication

Create the public `FU-max-boop/agentrunproof` repository, push the clean history, let CI complete, create a signed or annotated release tag, publish artifacts only after checksum and metadata verification, and make the evidence links stable.

The milestone is complete when an unauthenticated reader can install the package, run the quickstart without an API key, validate the evidence bundle, and inspect green CI.

### Milestone 5: external counterexample and adoption

Run systematic scenario matrices against current upstream `main`. Minimize any failure, independently verify it, and report it with neutral wording and a conventional pytest reproducer. After external confirmation, propose only the smallest upstream test or documentation integration justified by evidence.

The milestone is complete when the charter's Gate 4 is satisfied.

## Surprises & Discoveries

- The public `ScriptedModel` family landed on upstream `main` after v0.20.0. The project therefore cannot make its first released contract depend exclusively on `agents.testing` until a published SDK version contains it.
- The released `Model` interface is unchanged between v0.19.0 and v0.20.0 at the model-call boundary relevant to the initial harness, making a narrow released-interface adapter feasible.
- Closest existing projects either replay simplified completed trajectories, operate at HTTP transport level, or provide a heavier production replay platform. None found so far combines real Runner execution, state invariants, fail-closed redaction, and integrity-bound counterexamples.
- A first isolated-wheel rehearsal distinguished the exact #4322 release boundary without
  importing upstream test helpers: `openai-agents==0.19.4` failed both runtime paths and
  `0.20.0` passed both. This validates the version-worker architecture before evidence is
  generated from a clean commit.
- The approval and guardrail histories require several real Runner invocations. Moving them
  through one generic multi-phase engine closed a proof gap that a standalone history worker
  would otherwise have hidden from certificate validation.
- A paused `RunResult` on both released 0.20.0 and current `main` returns sibling `RunState`
  objects whose approval state is aliased: approving one sibling mutates and authorizes the
  other. JSON-restored siblings are independent, making the direct-vs-serialized mismatch a
  narrow, falsifiable current counterexample.

## Decision Log

- **2026-08-14 — Product boundary:** Build runtime state conformance, not a general agent framework, eval service, or tracing backend. This matches recurring upstream failures and leaves a defensible standalone boundary.
- **2026-08-14 — Implementation baseline:** Target released `openai-agents` 0.20.x through the public `Model` interface. Use `agents.testing.ScriptedModel` only through an optional adapter when present.
- **2026-08-14 — First release scope:** Standard text Runner only. Realtime, Voice, Sandbox, MCP wire conformance, and production cassette recording remain out of scope.
- **2026-08-14 — Evidence semantics:** Certificates are content-addressed integrity records, not cryptographic attestations of execution. Public claims require a clean commit plus CI/release anchoring.
- **2026-08-14 — Adoption target:** Seek upstream test/docs/dev-tool recognition, not a default runtime dependency.
- **2026-08-14 — Streaming boundary:** v0.1 claims post-run parity for a terminal-event scripted stream, not token/delta timing, backpressure, or cancellation behavior.
- **2026-08-14 — Evidence commits:** Canonical matrix generation is anchored to source commit C; the child evidence commit E may add only the matrix and final bundle marker.
- **2026-08-14 — Narrow v0.1.1 scope extension:** A concrete released/current counterexample
  justifies adding one sibling-`RunState` approval-isolation scenario. It will use only public SDK
  APIs and content-addressed observations; generalized object snapshot isolation remains out of
  scope. Certificate v1 may grow only strict backward-compatible fields that old certificates do
  not need.

## Outcomes & Retrospective

Not yet complete.
