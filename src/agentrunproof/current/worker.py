from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from ..builtins import get_scenario
from ..certificate import build_certificate, certificate_json
from ..engine import run_scenario


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agentrunproof.current.worker")
    parser.add_argument("scenario")
    args = parser.parse_args(argv)
    try:
        proof = asyncio.run(run_scenario(get_scenario(args.scenario)))
        certificate = build_certificate(proof)
        sys.stdout.write(certificate_json(certificate))
        return 0 if proof.status == "PASS" else 1
    except Exception as error:
        payload = {
            "schema_version": "agentrunproof.upstream-worker-error/v1",
            "error": {"type": type(error).__name__},
        }
        print(json.dumps(payload, allow_nan=False, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
