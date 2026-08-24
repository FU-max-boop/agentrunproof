# CLI reference

The `agentrunproof` CLI runs the packaged provider-free scenarios and validates AgentRunProof JSON
evidence. `python -m agentrunproof` is an equivalent entry point and accepts the same arguments.
Use `agentrunproof --help`, `agentrunproof COMMAND --help`, and `agentrunproof --version` for
locally installed command metadata.

## Commands

<!-- reference-table:commands:start -->
| Command | Arguments | Purpose |
| --- | --- | --- |
| `list-scenarios` | None | Print the ID and description of every built-in scenario. |
| `probe` | `SCENARIO [--certificate PATH]` | Run one built-in scenario through its declared real-Runner variants; optionally write its JSON certificate. |
| `check-certificate` | `CERTIFICATE` | Strictly validate one certificate without executing the scenario. |
| `check-history-matrix` | `MATRIX` | Validate the canonical released-SDK historical regression matrix. |
| `check-history-bundle` | `BUNDLE` | Validate a historical evidence bundle and its bound matrix member. |
| `check-current-bundle` | `BUNDLE` | Validate the canonical current counterexample bundle and adjacent members. |
| `check-upstream-bundle` | `BUNDLE` | Validate a released-versus-upstream comparison bundle and adjacent members. |
<!-- reference-table:commands:end -->

For `probe`, omitting `--certificate` still runs the scenario and prints diagnostics but does not
write any evidence file. Supply the option whenever another process needs to consume the result.

Examples:

```bash
agentrunproof list-scenarios
agentrunproof probe basic-tool-session-parity --certificate proof.json
agentrunproof check-certificate proof.json
agentrunproof check-history-matrix evidence/history/v1/matrix.json
agentrunproof check-history-bundle evidence/history/v1/bundle.json
agentrunproof check-current-bundle \
  evidence/current/runstate-sibling-approval-isolation/v1/bundle.json
agentrunproof check-upstream-bundle evidence/upstream-comparison/v2/bundle.json
```

The three bundle commands validate referenced files relative to the directory containing the
bundle. Copy the complete evidence directory rather than a lone bundle marker when checking it in
another workspace.

## Built-in scenarios

<!-- reference-table:scenarios:start -->
| Scenario ID | Contract |
| --- | --- |
| `basic-tool-session-parity` | One deterministic function-tool call and final output remain coherent across non-streaming and streaming Runner modes. |
| `handoff-session-filtered-view-parity` | One filtered handoff removes tool records before nesting history, giving the specialist a structured tool-free view while the durable session retains complete tool causality in both Runner modes. |
| `runstate-recursive-agent-tool-approval-routing` | One flattened approval routes through two `Agent.as_tool` checkpoints while an untouched direct sibling remains pending. |
| `runstate-recursive-agent-tool-approval-serialization` | One flattened approval survives `RunState` JSON restoration and routes through two `Agent.as_tool` checkpoints. |
| `runstate-sibling-approval-isolation` | Approving one direct `RunState` sibling does not mutate another sibling created from the same result. |
<!-- reference-table:scenarios:end -->

Scenario results are contracts over SDK runtime behavior, not model-quality scores. A built-in may
intentionally fail on a released SDK version when it reproduces a known counterexample. Inspect
the written certificate's `overall_status`, invariant results, runtime versions, source
provenance, and limitations before making a claim.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The probe's overall status is `PASS`, or the requested list/check command completed successfully. |
| `1` | A probe completed but its overall status was not `PASS`; inspect the certificate for `FAIL` or `NOT_RUN` evidence. |
| `2` | Arguments, paths, JSON, schema, hashes, or semantic evidence were invalid or unverifiable. |

An unexpected process failure outside these handled cases may use a different shell exit status.
If `probe` exits `1` without `--certificate`, no JSON artifact was saved; rerun the same probe with
`--certificate PATH` before handing the failure to automated analysis.

## Output contract for automation

CLI stdout and stderr are human-facing diagnostics. Their wording and spacing are not a stable
machine protocol, so do not scrape `PASS`, `VALID`, or certificate IDs from terminal text.
Automation should pass `--certificate`, require the expected exit code, then parse and independently
validate the JSON artifact:

```bash
agentrunproof probe basic-tool-session-parity --certificate proof.json
agentrunproof check-certificate proof.json
```

The JSON certificate is canonical, content-addressed, and semantically revalidated by
`check-certificate`. The history/current/comparison bundle JSON formats add exact artifact,
environment, and source relationships for repository evidence workflows.

## Provider-free and network boundary

Built-in probes use deterministic scripted model output, make no model-provider API request, need
no API key, and run with SDK tracing disabled. This is a provider-free execution boundary, not an
OS network sandbox. Installing packages may access package indexes, and arbitrary custom scenario
tools or hooks can perform network I/O. The ordinary CLI does not claim to block those paths.

Canonical repository evidence uses additional isolated workers, pinned artifacts, and a Python
socket-deny guard during scenario execution. That stronger evidence workflow is implemented by the
repository scripts and CI; running `agentrunproof probe` alone does not reproduce the entire
canonical publication environment.
