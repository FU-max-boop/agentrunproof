from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from ._version import __version__
from .builtins import SCENARIOS, get_scenario
from .certificate import (
    CertificateError,
    build_certificate,
    load_certificate,
    write_certificate,
)
from .current.bundle import load_current_bundle
from .current.comparison import load_upstream_comparison
from .engine import run_scenario
from .history.bundle import load_history_bundle
from .history.evidence import load_history_matrix


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentrunproof",
        description="Deterministic runtime conformance for the OpenAI Agents SDK.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-scenarios", help="List built-in scenarios.")
    list_parser.set_defaults(handler=_list_scenarios)

    probe = subparsers.add_parser("probe", help="Run one built-in conformance scenario.")
    probe.add_argument("scenario", choices=sorted(SCENARIOS))
    probe.add_argument("--certificate", type=Path)
    probe.set_defaults(handler=_probe)

    check = subparsers.add_parser(
        "check-certificate", help="Validate a certificate without executing a scenario."
    )
    check.add_argument("certificate", type=Path)
    check.set_defaults(handler=_check_certificate)

    check_history = subparsers.add_parser(
        "check-history-matrix",
        help="Validate the canonical historical regression matrix.",
    )
    check_history.add_argument("matrix", type=Path)
    check_history.set_defaults(handler=_check_history_matrix)

    check_bundle = subparsers.add_parser(
        "check-history-bundle",
        help="Validate a historical evidence bundle and its matrix member.",
    )
    check_bundle.add_argument("bundle", type=Path)
    check_bundle.set_defaults(handler=_check_history_bundle)

    check_current_bundle = subparsers.add_parser(
        "check-current-bundle",
        help="Validate the canonical current counterexample bundle.",
    )
    check_current_bundle.add_argument("bundle", type=Path)
    check_current_bundle.set_defaults(handler=_check_current_bundle)

    check_upstream_bundle = subparsers.add_parser(
        "check-upstream-bundle",
        help="Validate the released-versus-upstream comparison bundle.",
    )
    check_upstream_bundle.add_argument("bundle", type=Path)
    check_upstream_bundle.set_defaults(handler=_check_upstream_bundle)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (CertificateError, KeyError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


def _list_scenarios(args: argparse.Namespace) -> int:
    del args
    for scenario_id, scenario in sorted(SCENARIOS.items()):
        print(f"{scenario_id}\t{scenario.description}")
    return 0


def _probe(args: argparse.Namespace) -> int:
    scenario = get_scenario(args.scenario)
    proof_run = asyncio.run(run_scenario(scenario))
    certificate = build_certificate(proof_run)
    if args.certificate is not None:
        write_certificate(args.certificate, certificate)
    print(f"{proof_run.status} {scenario.scenario_id}")
    for result in proof_run.invariant_results:
        print(f"  {result.status:7} {result.name}: {result.reason}")
    print(f"certificate_id: {certificate['certificate_id']}")
    if args.certificate is not None:
        print(f"written: {args.certificate}")
    return 0 if proof_run.status == "PASS" else 1


def _check_certificate(args: argparse.Namespace) -> int:
    certificate = load_certificate(args.certificate)
    print(f"VALID {certificate['certificate_id']} {certificate['overall_status']}")
    return 0


def _check_history_matrix(args: argparse.Namespace) -> int:
    matrix = load_history_matrix(args.matrix)
    print(f"VALID {matrix['matrix_id']} history-matrix")
    return 0


def _check_history_bundle(args: argparse.Namespace) -> int:
    bundle = load_history_bundle(args.bundle)
    print(f"VALID {bundle['bundle_id']} history-bundle")
    return 0


def _check_current_bundle(args: argparse.Namespace) -> int:
    bundle = load_current_bundle(args.bundle)
    print(f"VALID {bundle['bundle_id']} current-bundle")
    return 0


def _check_upstream_bundle(args: argparse.Namespace) -> int:
    bundle = load_upstream_comparison(args.bundle)
    print(f"VALID {bundle['bundle_id']} upstream-comparison")
    return 0
