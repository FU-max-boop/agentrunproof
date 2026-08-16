"""Run a real Agents SDK tool loop without a model provider or API key."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import Literal

from agents import Agent, Runner, function_tool
from agents.run import RunConfig

from agentrunproof import DeterministicModel, assistant_message, function_call

RunMode = Literal["run", "streamed"]


@dataclass(frozen=True)
class DemoResult:
    """The small set of facts the demo proves about the completed run."""

    mode: RunMode
    final_output: str
    tool_invocations: tuple[str, ...]
    model_calls: int

    def as_json(self) -> dict[str, object]:
        return {
            "final_output": self.final_output,
            "mode": self.mode,
            "model_calls": self.model_calls,
            "provider_requests": 0,
            "script_consumed": True,
            "tool": {
                "arguments": list(self.tool_invocations),
                "invocations": len(self.tool_invocations),
                "name": "lookup_fixture",
            },
        }


async def run_demo(mode: RunMode = "run") -> DemoResult:
    """Drive one deterministic function-tool call through the real SDK Runner."""

    tool_invocations: list[str] = []

    @function_tool
    def lookup_fixture(key: str) -> str:
        """Return the value for one public, synthetic fixture key."""

        tool_invocations.append(key)
        return "42" if key == "alpha" else "not-found"

    model = DeterministicModel(
        [
            [
                function_call(
                    "lookup_fixture",
                    {"key": "alpha"},
                    call_id="provider-free-demo-call",
                )
            ],
            [assistant_message("The fixture value is 42.")],
        ]
    )
    agent = Agent(
        name="Provider-free demo",
        instructions="Look up the requested fixture, then report its value.",
        model=model,
        tools=[lookup_fixture],
    )
    run_config = RunConfig(tracing_disabled=True)

    if mode == "streamed":
        streamed_result = Runner.run_streamed(
            agent,
            "Look up the synthetic fixture named alpha.",
            run_config=run_config,
        )
        async for _ in streamed_result.stream_events():
            pass
        final_output = streamed_result.final_output
    else:
        run_result = await Runner.run(
            agent,
            "Look up the synthetic fixture named alpha.",
            run_config=run_config,
        )
        final_output = run_result.final_output

    model.assert_complete()
    if final_output != "The fixture value is 42.":
        raise AssertionError(f"Unexpected final output: {final_output!r}")
    if tool_invocations != ["alpha"]:
        raise AssertionError(
            f"Expected exactly one lookup_fixture('alpha') call, got {tool_invocations!r}"
        )
    if len(model.calls) != 2:
        raise AssertionError(f"Expected two model turns, got {len(model.calls)}")
    expected_streamed = mode == "streamed"
    if any(call.streamed is not expected_streamed for call in model.calls):
        raise AssertionError(f"The model was not called consistently in {mode!r} mode")

    return DemoResult(
        mode=mode,
        final_output=final_output,
        tool_invocations=tuple(tool_invocations),
        model_calls=len(model.calls),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a real OpenAI Agents SDK tool loop with no provider request."
    )
    parser.add_argument(
        "--mode",
        choices=("run", "streamed"),
        default="run",
        help="Use Runner.run (default) or Runner.run_streamed.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = asyncio.run(run_demo(args.mode))
    print(json.dumps(result.as_json(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
