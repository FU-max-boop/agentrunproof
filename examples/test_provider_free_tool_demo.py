from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from provider_free_tool_demo import run_demo


@pytest.mark.parametrize("mode", ["run", "streamed"])
def test_real_runner_executes_local_tool_exactly_once(mode: str) -> None:
    result = asyncio.run(run_demo(mode))

    assert result.mode == mode
    assert result.final_output == "The fixture value is 42."
    assert result.tool_invocations == ("alpha",)
    assert result.model_calls == 2


@pytest.mark.parametrize("mode", ["run", "streamed"])
def test_demo_runs_directly_without_an_api_key(mode: str) -> None:
    examples_dir = Path(__file__).resolve().parent
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    completed = subprocess.run(
        [sys.executable, str(examples_dir / "provider_free_tool_demo.py"), "--mode", mode],
        cwd=examples_dir.parent,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(completed.stdout)
    assert summary == {
        "final_output": "The fixture value is 42.",
        "mode": mode,
        "model_calls": 2,
        "provider_requests": 0,
        "script_consumed": True,
        "tool": {
            "arguments": ["alpha"],
            "invocations": 1,
            "name": "lookup_fixture",
        },
    }
