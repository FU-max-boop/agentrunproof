from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import platform
import sys
from collections.abc import Sequence
from typing import Any

from .._canonical import to_json_value
from .._version import __version__
from .scenarios import SCENARIOS


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agentrunproof.history.worker")
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    args = parser.parse_args(argv)
    scenario = SCENARIOS[args.scenario]
    try:
        result = asyncio.run(scenario())
        payload: dict[str, Any] = {
            "schema_version": "agentrunproof.history-observation/v1",
            "agentrunproof_version": __version__,
            "openai_agents_version": importlib.metadata.version("openai-agents"),
            "python": platform.python_version(),
            "result": result,
        }
        print(json.dumps(to_json_value(payload), ensure_ascii=False, sort_keys=True))
        return 0 if result["overall_status"] == "PASS" else 1
    except Exception as error:
        payload = {
            "schema_version": "agentrunproof.history-observation/v1",
            "agentrunproof_version": __version__,
            "openai_agents_version": _distribution_version(),
            "python": platform.python_version(),
            "worker_error": {"type": type(error).__name__},
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 2


def _distribution_version() -> str | None:
    try:
        return importlib.metadata.version("openai-agents")
    except importlib.metadata.PackageNotFoundError:
        return None


if __name__ == "__main__":
    sys.exit(main())
