# Changelog

All notable changes to AgentRunProof will be documented here.

## Unreleased

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
