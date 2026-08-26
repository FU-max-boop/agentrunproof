# AgentRunProof documentation

AgentRunProof is a deterministic conformance harness for OpenAI Agents SDK `Runner` behavior. It
runs scripted, provider-free scenarios through the real `Runner.run()` and
`Runner.run_streamed()` paths, evaluates declared runtime invariants, and can write a
content-addressed JSON certificate.

## Start here

- [Python API reference](api-reference.md) documents every supported top-level import and the
  schema-bound `agentrunproof.current` evidence API.
- [CLI reference](cli-reference.md) documents every command, built-in scenario, exit code, and
  automation boundary.
- [OpenAI Agents integration guide](openai-agents.md) shows how to build a custom provider-free
  real-Runner contract.
- [Isolated CI adoption guide](ci-adoption.md) provides a test-only downstream integration that
  does not add AgentRunProof to runtime metadata or the project lockfile.
- [Paid design-partner pilot](design-partner-pilot.md) defines the fixed commercial experiment,
  customer-owned CI boundary, price, exclusions, and success criteria.
- [RunState case study](case-study-runstate.md) follows a released failure through upstream fixes
  and independently checkable evidence.
- [Examples](../examples/README.md) contains copyable local and CI-oriented samples.

For the fastest smoke test, follow the [30-second local check](../README.md#30-second-local-check).

## Scope and trust boundary

AgentRunProof evaluates SDK runtime semantics for the exact scenarios and invariants named in a
certificate. It does not evaluate model-output quality, authenticate a publisher, or prove that a
recorded command actually ran. Built-in scenarios do not call a model provider and need no API
key, but provider-free does not mean network-sandboxed: dependency installation and arbitrary
custom tools or hooks may use the network.

The JSON certificate is the machine-readable interface. CLI stdout is concise human-facing
diagnostic output and is not a stable protocol; automation should read the written certificate or
validated bundle instead.
