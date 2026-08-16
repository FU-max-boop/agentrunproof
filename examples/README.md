# Provider-free real Runner demo

This example drives the real OpenAI Agents SDK `Runner` through a two-turn
function-tool loop using AgentRunProof's `DeterministicModel`. It makes exactly
one local tool call, consumes the complete model script, and makes zero model
provider requests.

After installing AgentRunProof, run either SDK execution path from the repository
root. No API key is required:

```bash
python examples/provider_free_tool_demo.py
python examples/provider_free_tool_demo.py --mode streamed
```

The command prints a machine-readable summary like this:

```json
{
  "final_output": "The fixture value is 42.",
  "mode": "run",
  "model_calls": 2,
  "provider_requests": 0,
  "script_consumed": true,
  "tool": {
    "arguments": [
      "alpha"
    ],
    "invocations": 1,
    "name": "lookup_fixture"
  }
}
```

Tracing is explicitly disabled so the demo stays local even if the surrounding
environment has an SDK trace exporter configured. The `run` and `streamed`
variants use the same deterministic script and assert the same final output and
exactly-once side effect.

To run the example's tests:

```bash
pytest -q examples/test_provider_free_tool_demo.py
```
