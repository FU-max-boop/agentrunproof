"""Copyable downstream contract test using the real Runner and a local model script."""

from __future__ import annotations

import asyncio
from typing import Literal

import pytest
from agents import Agent, FunctionTool, Runner, function_tool
from agents.run import RunConfig

from agentrunproof import DeterministicModel, assistant_message, function_call

RunMode = Literal["run", "streamed"]


def _build_tool(invocations: list[str]) -> FunctionTool:
    """Replace this function with the downstream library's tool adapter or wrapper."""

    @function_tool
    def lookup_fixture(key: str) -> str:
        """Return one synthetic fixture value."""

        invocations.append(key)
        return "42"

    return lookup_fixture


async def _exercise_runner(mode: RunMode) -> tuple[str, list[str]]:
    invocations: list[str] = []
    tool = _build_tool(invocations)
    model = DeterministicModel(
        [
            [function_call(tool.name, {"key": "alpha"}, call_id="downstream-contract-call")],
            [assistant_message("The fixture value is 42.")],
        ]
    )
    agent = Agent(
        name="Downstream adapter contract",
        instructions="Call the fixture tool once, then report its value.",
        model=model,
        tools=[tool],
    )
    run_config = RunConfig(tracing_disabled=True)

    if mode == "streamed":
        result = Runner.run_streamed(agent, "Look up alpha.", run_config=run_config)
        async for _ in result.stream_events():
            pass
    else:
        result = await Runner.run(agent, "Look up alpha.", run_config=run_config)

    model.assert_complete()
    assert len(model.calls) == 2
    assert all(call.streamed is (mode == "streamed") for call in model.calls)
    return result.final_output, invocations


@pytest.mark.parametrize("mode", ["run", "streamed"])
def test_adapter_tool_runs_exactly_once_through_real_runner(mode: RunMode) -> None:
    output, invocations = asyncio.run(_exercise_runner(mode))

    assert output == "The fixture value is 42."
    assert invocations == ["alpha"]
