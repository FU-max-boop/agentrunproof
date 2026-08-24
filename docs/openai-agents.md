# AgentRunProof for the OpenAI Agents SDK

AgentRunProof is a community-maintained runtime-conformance tool for the
[OpenAI Agents SDK for Python](https://github.com/openai/openai-agents-python). It runs the real
`Runner` against deterministic, local model scripts, checks declared state and side-effect
invariants, and writes content-addressed JSON certificates.

AgentRunProof is not an OpenAI project, an SDK dependency, or an official endorsement. Its public
upstream references document diagnostic use of the tool; they do not mean that OpenAI has adopted
or certified it.

## How it complements `agents.testing`

Use the SDK's `agents.testing` utilities, when available in your SDK version, to write focused
application and SDK tests with scripted model behavior. AgentRunProof addresses a narrower,
cross-version problem: it packages repeatable runtime scenarios, compares streaming and
non-streaming paths, checks interruption and resume semantics, and emits records that another
process can validate without rerunning a provider request.

It does not replace ordinary `pytest` assertions or evaluate model quality. A useful workflow is to
use AgentRunProof to reduce a runtime inconsistency to a stable certificate, then add a conventional
SDK regression test for the fix.

## Compatibility

The released AgentRunProof 0.3.0 contract declares `openai-agents>=0.20.0,<0.23` on Python 3.10
through 3.14. Its packaged-wheel acceptance matrix covers exact SDK 0.20.0, 0.21.0, and 0.22.0
installations. Other patch, prerelease, or source revisions are not individually verified support
cells.

Install the released package in a fresh environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install agentrunproof
mkdir -p build
```

Built-in scenarios use deterministic local responses and make zero provider requests. They require
no API key. This guarantee covers the built-in scenarios, not arbitrary user-supplied Python code;
package installation may still contact the configured package index.

To reproduce an exact 0.3.0 acceptance cell, pin the package and SDK together:

```bash
python -m pip install "agentrunproof==0.3.0" "openai-agents==0.22.0"
```

Substitute SDK 0.20.0 or 0.21.0 to exercise the other packaged baselines.

## Three focused checks

### 1. Basic Runner, tool, session, and stream parity

```bash
agentrunproof probe basic-tool-session-parity \
  --certificate build/basic-tool-session-parity.json
```

This is the smallest supported smoke test and should pass on a compatible installation.

### 2. Sibling `RunState` approval isolation

```bash
agentrunproof probe runstate-sibling-approval-isolation \
  --certificate build/runstate-sibling-approval-isolation.json
```

On `openai-agents==0.20.0`, this probe intentionally records the released sibling-state
counterexample and exits `1`. That result is a detected invariant violation, not a CLI failure.

### 3. Recursive approval routing and JSON restoration

```bash
agentrunproof probe runstate-recursive-agent-tool-approval-routing \
  --certificate build/runstate-recursive-approval.json

agentrunproof probe runstate-recursive-agent-tool-approval-serialization \
  --certificate build/runstate-recursive-approval-serialization.json
```

These scenarios check that one exact approval reaches the protected tool through nested
`Agent.as_tool` checkpoints, including across `RunState.to_json()` / `RunState.from_json()`, without
executing the effect more than once.

## Provider-free tracing for integration tests

Supported SDKs 0.21 and 0.22 supply the public `agents.testing.ScriptedModel`; AgentRunProof
delegates deterministic execution to it when available and preserves a public-`Model` fallback on
SDK 0.20. Both paths can emit the SDK's normal generation span explicitly:

```python
from agents import Agent, Runner
from agentrunproof import DeterministicModel, assistant_message

model = DeterministicModel(
    [[assistant_message("done")]],
    emit_traces=True,
)
result = await Runner.run(Agent(name="instrumented test", model=model), "hello")
assert result.final_output == "done"
model.assert_complete()
```

This opt-in is intended for tracing and instrumentation test suites that need the real Runner but
must not call a model provider. It is disabled by default. AgentRunProof's own `run_scenario()`
continues to set `tracing_disabled=True`, so certificates do not capture or evaluate trace data.
Call `Runner.run()` or `Runner.run_streamed()` directly for observability tests. An installed SDK
trace processor can export spans or make network requests; configure the processor under test
before enabling traces.

## Check a certificate

```bash
agentrunproof check-certificate build/basic-tool-session-parity.json
```

A probe exits `0` for PASS and `1` for an observed invariant violation. Invalid input or evidence
that cannot be verified exits `2`. `check-certificate` validates the schema, content address, and
recorded semantic results; it does not rerun the SDK, authenticate the publisher, or prove that an
untrusted party executed the stated command. Public claims should therefore include the exact
source or package revision and a visible CI or release anchor.

## Supported boundary and non-goals

The certificate-v1 contract covers the standard text `Runner`, deterministic public-`Model` responses,
non-streaming and terminal-event streaming execution, selected `Session` observations, function
tools, and selected serializable `RunState` approval/resume transitions.

It does not cover model-output quality, trace-payload conformance, hosted evaluation, arbitrary
custom `Session` implementations, HTTP replay, production side-effect interception, token/delta timing,
backpressure, cancellation, Realtime, Voice, Sandbox, or MCP wire-protocol conformance. A
certificate is an integrity-bound diagnostic record, not a cryptographic attestation.

## Public upstream evidence

- The initial
  [report on #4409](https://github.com/openai/openai-agents-python/pull/4409#issuecomment-5291724795)
  described a sibling-`RunState` isolation counterexample; a later comment linked the immutable
  v0.1.1 certificate and release.
- Maintainer follow-up [#4413](https://github.com/openai/openai-agents-python/pull/4413) cites that
  report in its PR body and fixes the checkpoint-decision isolation defect.
- Merged follow-up [#4414](https://github.com/openai/openai-agents-python/pull/4414) cites the
  subsequent recursive finding. The
  [AgentRunProof validation on #4414](https://github.com/openai/openai-agents-python/pull/4414#issuecomment-5293587925)
  distinguishes its initial serialized-state failure from the revision merged as `50d65f65`.

These links show that AgentRunProof evidence was cited in upstream diagnosis and validation. They are
not a claim of official library adoption. The maintainer of the official v0.21 testing guide
[declined a community-tool listing](https://github.com/openai/openai-agents-python/pull/4381#issuecomment-5293704972)
to keep that page focused on SDK-maintained APIs, while explicitly welcoming future reproducible
findings backed by AgentRunProof.

Source, releases, evidence, and issue reporting are available in the
[AgentRunProof repository](https://github.com/FU-max-boop/agentrunproof).
