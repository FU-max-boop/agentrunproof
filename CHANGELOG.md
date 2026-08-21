# Changelog

All notable changes to AgentRunProof will be documented here.

## 0.3.0 - 2026-08-21

- Extend the declared dependency window to `openai-agents>=0.20.0,<0.23` and add exact 0.22.0
  packaged-wheel coverage to the Python 3.10–3.14 CI matrix alongside 0.20.0 and 0.21.0.
- Treat SDK 0.22's output-guardrail replacement semantically: preserve call/output linkage and
  require the rejected raw tool result to be absent without pinning the SDK's replacement text;
  use scenario/case revision 2 while older history keeps revision 1.
- Preserve certificate v1 plus all existing historical evidence and lock files, and require a
  fresh v3 comparison bundle bound to the 0.3.0 source and release wheel.
- Add a provider-free real-`Runner` tool example, contribution and security guidance, structured
  issue and pull-request templates, and a five-minute upstream `RunState` case study.
- Rework the project landing page around a 30-second PyPI check, supported SDK boundaries, honest
  upstream impact, and a clear comparison with the SDK's public `ScriptedModel`.
- Improve package discovery metadata and add a reusable social-preview asset.

## 0.2.0 - 2026-08-15

- Verify released `openai-agents` 0.20.0 and 0.21.0 across Python 3.10–3.14 with an exact
  packaged-wheel CI matrix.
- Add `DeterministicModel(..., emit_traces=True)` for provider-free observability integration
  tests. SDK 0.21 delegates to its public `agents.testing.ScriptedModel`; the 0.20 fallback emits
  the same generation-span boundary without changing the default no-generation-span behavior.
- Keep certificate execution tracing-disabled and preserve certificate-v1 plus all immutable v0.1
  evidence contracts.
- Add a fresh canonical upstream-comparison release gate whose evidence binds the v0.2.0 wheel.

## 0.1.2 - 2026-08-14

- Delegate deterministic model execution to the SDK's public `agents.testing.ScriptedModel`
  when that API is available, while preserving the 0.20-compatible fallback and normalized
  certificate semantics.
- Add `runstate-recursive-agent-tool-approval-routing`, which verifies a flattened approval
  through two `Agent.as_tool` checkpoints while an untouched sibling remains interrupted.
- Add `runstate-recursive-agent-tool-approval-serialization`, which verifies that a recursive
  approval applied after restoration reaches the protected effect across the `RunState` JSON
  boundary.
- Add generation and strict checking for upstream-comparison bundles that distinguish release and
  source wheels by content hash and exact Git provenance. Canonical v0.1.2 evidence remains a
  release gate rather than a checked-in development claim.
- Add a dedicated OpenAI Agents usage page that documents compatibility, no-provider-request
  scenarios, certificate limits, upstream evidence, and the project's external status.

## 0.1.1 - 2026-08-14

- Add `runstate-sibling-approval-isolation`, a public no-network scenario that detects approval
  decisions leaking between two direct `RunState` snapshots from one interrupted result.
- Add the `state_fork_isolation` invariant and strict, backward-compatible certificate-v1 fields
  for sibling decisions and subject-state digests.
- Preserve validation of the immutable v0.1.0 historical evidence bundle.

## 0.1.0 - 2026-08-14

- Add a deterministic public-`Model` harness over real streaming and non-streaming Runner paths.
- Add generic multi-phase execution with public `RunState` JSON reconstruction, exact approval decisions, per-phase probes, and session replay checks.
- Add strict content-addressed certificates with public/private payload profiles, canonical JSON, semantic recomputation, source provenance, and tamper tests.
- Add a strict six-run historical matrix for upstream issues #4322, #4244, and #4125.
- Add hash-locked Linux/CPython 3.12 historical environments plus generation and verification for a final history bundle marker.
- Add Python 3.10–3.14 CI, package metadata, and release-candidate documentation.
