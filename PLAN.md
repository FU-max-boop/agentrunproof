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
- [x] (2026-08-14) Published the public repository, immutable `v0.1.0` and `v0.1.1`
  GitHub releases, and both releases on PyPI through Trusted Publishing after the package and
  canonical Linux evidence jobs passed.
- [x] (2026-08-14) Added a public-API, no-network two-edge `Agent.as_tool` approval-routing
  scenario. It records a precise FAIL on merged upstream commit `0b93ce8` and all requested
  invariants PASS on #4414's merged correction `50d65f65`; the immutable v0.1.2 comparison bundle
  pins both boundaries.
- [x] (2026-08-14) Reported the `RunResult.to_state()` sibling-approval isolation counterexample
  with a conventional reproducer and the immutable AgentRunProof v0.1.1 certificate. The
  maintainer acknowledged it and merged follow-up PR #4413, whose body cites the report.
- [x] (2026-08-14) Obtained maintainer-level external recognition through that acknowledgement,
  cited fix, and the public AgentRunProof validation posted on #4413. This is recognition of the
  diagnostic evidence, not adoption as an SDK dependency.
- [x] (2026-08-14) Added a durable-state recursive scenario that distinguished the initial #4414
  head from its revision: `9dc7da9` remained interrupted after JSON restoration, while `1725a898`
  passed the direct and restored-approval scenarios and was squash-merged as `50d65f65`. The
  upstream 24-case test covers the wider approval/rejection timing matrix. Posted the independent
  validation on #4414 before merge.
- [x] (2026-08-14) Proposed a minimal community-tool entry for AgentRunProof on the official v0.21
  testing-guide PR #4381. The maintainer declined the listing to keep that guide focused on
  SDK-maintained APIs, while explicitly welcoming future reproducible findings backed by the tool.
- [x] (2026-08-15) Published v0.2.0 with packaged-wheel coverage for SDK 0.20.0 and 0.21.0, an
  opt-in provider-free generation span for observability integration tests, and a fresh canonical
  comparison bundle bound to the v0.2.0 wheel. The immutable GitHub Release and PyPI artifacts
  have matching wheel and sdist digests, and fresh installs pass against both exact SDK baselines.
- [x] (2026-08-21) Prepared the v0.3.0 source release candidate for SDK 0.22: widened metadata to
  `<0.23`, added the exact 0.22.0 packaged-wheel cells, and made the output-guardrail history check
  require linked redacted replay without matching replacement prose. One built wheel passed the
  full suite locally on CPython 3.12 against exact SDK 0.20.0, 0.21.0, and 0.22.0. The full
  Python 3.10–3.14 CI matrix and a fresh release-bound v3 evidence bundle remain publication gates.

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
- OpenAI Agents v0.21.0 now publishes that testing API. Delegating to it gives AgentRunProof a
  stable latest-SDK backend, while the 0.20 fallback preserves a single cross-version fixture API.
- The released `Model` interface is unchanged between v0.19.0 and v0.20.0 at the model-call boundary relevant to the initial harness, making a narrow released-interface adapter feasible.
- Closest existing projects either replay simplified completed trajectories, operate at HTTP transport level, or provide a heavier production replay platform. None found so far combines real Runner execution, state invariants, fail-closed redaction, and integrity-bound counterexamples.
- A first isolated-wheel rehearsal distinguished the exact #4322 release boundary without
  importing upstream test helpers: `openai-agents==0.19.4` failed both runtime paths and
  `0.20.0` passed both. This validates the version-worker architecture before evidence is
  generated from a clean commit.
- The approval and guardrail histories require several real Runner invocations. Moving them
  through one generic multi-phase engine closed a proof gap that a standalone history worker
  would otherwise have hidden from certificate validation.
- A paused `RunResult` on released 0.20.0 and upstream commit `3e87dc8` returned sibling `RunState`
  objects whose approval state was aliased: approving one sibling mutated and authorized the
  other. JSON-restored siblings remained independent. Follow-up #4413 fixed that boundary in
  commit `0b93ce8`.
- OpenAI Agents 0.22 intentionally replaces a tool result rejected by a terminal output guardrail
  before saving and replaying the session. The call/output pair and follow-up execution remain
  intact, so compatibility depends on redaction semantics rather than the SDK's replacement text.

## Decision Log

- **2026-08-14 — Product boundary:** Build runtime state conformance, not a general agent framework, eval service, or tracing backend. This matches recurring upstream failures and leaves a defensible standalone boundary.
- **2026-08-14 — Implementation baseline:** Target released `openai-agents` 0.20.0 through the public `Model` interface. Use `agents.testing.ScriptedModel` only through an optional adapter when present.
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
- **2026-08-14 — Recursive approval-routing extension:** The merged upstream isolation fix in
  #4413 leaves a concrete two-`Agent.as_tool` approval-routing boundary, so one built-in scenario
  may retain and resume an explicitly named direct sibling state. Certificate v1 gains only
  optional, backward-compatible phase and transition fields that bind the saved branch to its
  content digest; generalized branching and arbitrary workflow replay remain out of scope.
- **2026-08-14 — Durable recursive routing extension:** Automated and AgentRunProof review of
  #4414 proved that the same explicit approval can remain unresolved only after a public
  `RunState` JSON round trip. Add one fixed-shape serialization scenario using the existing phase
  and transition contract; do not generalize it into arbitrary checkpoint-graph traversal.
- **2026-08-14 — Upstream adoption boundary:** Keep AgentRunProof external after the maintainer
  declined a community-tool listing in #4381. Pursue acceptance through precise conventional
  regressions and reproducible validation of concrete gaps, not repeated documentation or default
  dependency requests.
- **2026-08-15 — v0.2 compatibility and interoperability:** Extend the packaged support matrix to
  exact `openai-agents` 0.20.0 and 0.21.0 release baselines. Keep deterministic model generation
  spans disabled by default,
  but expose an explicit opt-in that emits the SDK's ordinary generation span so observability
  integrations can exercise a real, provider-free `Runner`. This is an interoperability surface,
  not a tracing evaluator or backend; certificate semantics and the provider-free built-ins remain
  unchanged. A tested cell is claimed only after the packaged wheel runs against that exact SDK
  release.
- **2026-08-21 — v0.3 SDK 0.22 compatibility:** Extend the declared window to
  `openai-agents>=0.20,<0.23` and require packaged-wheel CI against the exact 0.20.0, 0.21.0, and
  0.22.0 releases. SDK 0.22 intentionally redacts a terminal tool result rejected by an output
  guardrail before durable session replay. The guardrail-history contract will therefore preserve
  call/output linkage while checking that the rejected raw result is absent; it must not depend on
  the SDK's human-readable replacement text. The redacted 0.22 family uses scenario/case revision
  2, while 0.19–0.21 retain revision 1 and their existing raw-output fingerprints. Existing
  signed-off evidence and historical lock files remain immutable. A v0.3 release remains blocked
  until fresh clean-commit evidence and all release gates exist; documentation and workflow
  preparation must not imply that 0.3.0 has been published.

## Outcomes & Retrospective

The initial Gates 0–4 and external-recognition goal are complete: released evidence distinguished
historical and current defects, and OpenAI Agents maintainers cited the findings in merged fixes.
That is evidence-level recognition, not library adoption. The next outcome is direct reuse of
AgentRunProof in an independent project's test or CI path, while preserving the narrow runtime
contract.
