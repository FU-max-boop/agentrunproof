# From one leaked approval to two merged fixes

## A five-minute AgentRunProof case study

On 14 August 2026, a small deterministic scenario exposed a state-ownership bug in
`openai-agents==0.20.0`. The report was cited by a maintainer-authored upstream fix, validation of
that fix exposed a deeper recursive boundary, and a second maintainer-authored fix closed the live
and serialized paths.

The precise result is **maintainer citation plus two merged upstream fixes**. OpenAI has not adopted
AgentRunProof as a dependency, official test tool, or endorsed project. AgentRunProof remains an
independent, community-maintained conformance harness.

## The bug: two states, one hidden authority

An interrupted result can be converted into resumable `RunState` objects. Two calls look like two
independent checkpoints:

```python
paused = await Runner.run(agent, "start")

decided = paused.to_state()
untouched = paused.to_state()

decided.approve(decided.get_interruptions()[0])
resumed = await Runner.run(agent, untouched)
```

The expected contract is simple: approving `decided` must not approve `untouched`. The untouched
branch should remain interrupted and the protected tool should execute zero times.

In the released `openai-agents==0.20.0` wheel, both states retained an alias to the same SDK-owned
approval and invocation ledger through their shared `context_wrapper`. Approving `decided` changed
the authority consulted by `untouched`; resuming the supposedly untouched branch executed the
protected effect once. This was separate from the public interruption-snapshot aliasing being fixed
in upstream [#4409](https://github.com/openai/openai-agents-python/pull/4409).

The [initial report](https://github.com/openai/openai-agents-python/pull/4409#issuecomment-5291724795)
reduced the behavior to this sibling-state invariant. A maintainer
[confirmed that it would be handled in a follow-up](https://github.com/openai/openai-agents-python/pull/4409#issuecomment-5291774141),
and the content-addressed v0.1.1
[certificate](https://github.com/FU-max-boop/agentrunproof/blob/e28ef9c8916d02b6d50f84a995401ab337ee0dbb/evidence/current/runstate-sibling-approval-isolation/v1/certificate.json)
and [bundle](https://github.com/FU-max-boop/agentrunproof/blob/e28ef9c8916d02b6d50f84a995401ab337ee0dbb/evidence/current/runstate-sibling-approval-isolation/v1/bundle.json)
made the released failure available without an API key. Their publication is recorded in the
[upstream thread](https://github.com/openai/openai-agents-python/pull/4409#issuecomment-5292251828).

## Why ordinary tests missed it

Each happy path was individually plausible. The defect appeared only when the test treated a
resumable result as a branching state machine rather than a single mutable object:

- A one-checkpoint approval test cannot reveal authority shared by two sibling checkpoints.
- Copying the returned `ToolApprovalItem` does not prove that the internal decision ledger is
  detached.
- One `Agent.as_tool()` edge does not exercise recursive ownership. Two or three edges do.
- A live-state fix need not survive `RunState.to_json()` followed by `RunState.from_json()`.
- Checking only completion misses the unsafe outcome: a protected effect can run on the wrong
  branch. Its count must be exactly one on the approved branch and zero on the untouched branch.
- `Runner.run()` and `Runner.run_streamed()` are separate execution paths; both need the same state
  and side-effect contract.

These are interaction gaps, not model-quality failures. More provider calls would only add noise.

## The causal ladder

The first follow-up, [#4413](https://github.com/openai/openai-agents-python/pull/4413), linked the
original report in its PR body and detached SDK-owned approval state when creating checkpoints. Its
merge commit, [`0b93ce8faa27`](https://github.com/openai/openai-agents-python/commit/0b93ce8faa27d4631df399fe48856b52a8fd9897),
fixed direct sibling isolation.

Running the wider contract against that exact commit found the next boundary: an approval flattened
through two or more nested `Agent.as_tool()` calls stayed interrupted and never reached the
protected tool. The
[#4413 validation comment](https://github.com/openai/openai-agents-python/pull/4413#issuecomment-5293064242)
reported the direct PASS and recursive FAIL separately.

The second follow-up, [#4414](https://github.com/openai/openai-agents-python/pull/4414), cited that
recursive finding. Its initial head,
[`9dc7da9f2bd`](https://github.com/openai/openai-agents-python/commit/9dc7da9f2bd7fcf4dc57e57a1f42a8bc2b595c9c),
fixed live recursive routing but still lost both approve and reject decisions after a JSON
round-trip. The revised head,
[`1725a8989ad`](https://github.com/openai/openai-agents-python/commit/1725a8989adcba536641cea1ef56a02d310c534e),
passed live and restored-state checks and was squash-merged as
[`50d65f65c367`](https://github.com/openai/openai-agents-python/commit/50d65f65c367a3b09dcd3313ee8d78471c35885e).
The exact validation scope is recorded in the
[#4414 validation comment](https://github.com/openai/openai-agents-python/pull/4414#issuecomment-5293587925).

| Exact target | Direct sibling isolation | Recursive live routing | Recursive routing after JSON restoration |
| --- | --- | --- | --- |
| PyPI `openai-agents==0.20.0` | **FAIL**: untouched branch executes the effect | Not claimed by the published comparison | Not claimed by the published comparison |
| `0b93ce8faa27` (#4413 merge) | **PASS** | **FAIL** at two `Agent.as_tool()` edges | Not claimed for this target |
| `9dc7da9f2bd` (#4414 initial head) | Not independently recorded here | **PASS** | **FAIL**: approve and reject remain interrupted |
| `1725a8989ad` / `50d65f65c367` | **PASS** | **PASS** at two and three edges | **PASS**, with decisions applied before or after restoration |

The canonical v0.2.0 bundle contains five isolated observations for the released wheel,
`0b93ce8faa27`, and `50d65f65c367`. The intermediate `9dc7da9f2bd` result is documented in the
public #4414 validation thread but is not a member of that bundle.

## What AgentRunProof added

AgentRunProof turned a surprising outcome into a narrow runtime contract:

1. **Real Runner:** the scenarios call the SDK's real `Runner.run()` and `Runner.run_streamed()`;
   they do not replace the orchestration code under test.
2. **Scripted model:** deterministic responses drive the required tool calls locally. AgentRunProof
   delegates to the SDK's public `agents.testing.ScriptedModel` where available and retains a
   public-`Model` fallback for SDK 0.20. No model provider or API key is required.
3. **Branch-aware assertions:** one interruption becomes two sibling states; only one receives an
   exact approval, while the other must remain pending.
4. **Recursive and durable variants:** the same decision is routed through nested
   `Agent.as_tool()` checkpoints, both live and across `RunState.to_json()` / `from_json()`.
5. **Stream parity and exactly-once effects:** non-streaming and terminal-event streaming must agree;
   the approved branch executes the synthetic effect once, and the untouched branch executes it
   zero times.
6. **Content-addressed evidence:** canonical JSON certificates bind normalized observations. The
   comparison bundle binds certificate hashes, clean AgentRunProof source provenance, exact upstream
   Git commits, source-built wheel hashes, the released wheel hash, and isolated environment locks.

That last step matters: a reviewer can distinguish a released-wheel failure from source revisions
that still report package version `0.20.0`. Target identity comes from commit and wheel bytes, not
the version string alone.

## Reproduce or verify

Reproduce the released sibling-state counterexample in a fresh environment (exit `1` is the
expected detected invariant violation):

```bash
python3.12 -m venv .venv-runstate
source .venv-runstate/bin/activate
python -m pip install "agentrunproof==0.2.0" "openai-agents==0.20.0"
mkdir -p build

agentrunproof probe runstate-sibling-approval-isolation \
  --certificate build/runstate-sibling.json || test "$?" -eq 1
agentrunproof check-certificate build/runstate-sibling.json
```

Independently validate the published five-run before/after record without rerunning the SDK:

```bash
git clone --branch v0.2.0 --depth 1 \
  https://github.com/FU-max-boop/agentrunproof.git
cd agentrunproof
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install .
agentrunproof check-upstream-bundle \
  evidence/upstream-comparison/v2/bundle.json
```

The published [AgentRunProof v0.2.0 release](https://github.com/FU-max-boop/agentrunproof/releases/tag/v0.2.0)
and [PyPI package](https://pypi.org/project/agentrunproof/0.2.0/) correspond to source tag `v0.2.0`,
which points to
[`a2744b06e6cb`](https://github.com/FU-max-boop/agentrunproof/commit/a2744b06e6cb977e723e4d9f24eccdda59c3c7a5).
Its [comparison bundle](https://github.com/FU-max-boop/agentrunproof/blob/a2744b06e6cb977e723e4d9f24eccdda59c3c7a5/evidence/upstream-comparison/v2/bundle.json)
has ID `sha256:da23130794081eaaaa0321953e596a62c5675a451711f552a1f31abb3f5a6349`
and was generated from clean harness source
[`d94443b5ef1f`](https://github.com/FU-max-boop/agentrunproof/commit/d94443b5ef1f832ce8e3673fb29f59fb67044a89).
The exact Linux x86_64 / CPython 3.12.13 regeneration completed in the public
[canonical CI job](https://github.com/FU-max-boop/agentrunproof/actions/runs/31874605394/job/94988518881),
which rebuilt the wheels, reran all five observations, and required byte-identical bundle members.

## Limits and recognition boundary

This evidence covers only the named `RunState` approval/resume scenarios and exact targets above.
It uses synthetic deterministic effects and terminal streaming events; it does not evaluate model
quality, provider behavior, token timing, backpressure, cancellation, Realtime, Voice, or general
SDK correctness. The socket-deny guard covers isolated scenario execution, not artifact download or
wheel building.

Content addressing detects modification and binds provenance inside the bundle, but it is not a
signature, publisher authentication, or proof that an untrusted machine executed the stated
command. Local bundle checking validates the record; it does not rerun the SDK.

Most importantly, the public upstream record supports this claim: **OpenAI Agents maintainers cited
the findings, authored the fixes, and merged #4413 and #4414.** It does not support the stronger
claim that OpenAI adopted, certified, endorsed, or depends on AgentRunProof.
