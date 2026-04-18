"""CLI entry: `uv run python -m sregym_agents.fault_verifier ...`.

Invoked by `bench/sregym/stress_test.py` as a subprocess so that the
submodule (which can't import sregym_agents directly) can still use the
agent-based verifier. Writes a `FaultVerification` JSON to --output.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from .verifier import run_fault_verifier


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SREGym agent-based fault verifier.")
    p.add_argument("--problem-id", required=True)
    p.add_argument("--root-cause", required=True, help="Free-text description of the expected fault.")
    p.add_argument("--app-name", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--kubeconfig-path", required=True, help="Path to the target cluster kubeconfig.")
    p.add_argument("--output", required=True, help="Path to write the FaultVerification JSON.")
    p.add_argument("--model", default=None, help="Optional model override for the agent CLI.")
    p.add_argument("--timeout-sec", type=int, default=300)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(argv if argv is not None else sys.argv[1:])

    result = run_fault_verifier(
        problem_id=ns.problem_id,
        root_cause=ns.root_cause,
        app_name=ns.app_name,
        namespace=ns.namespace,
        kubeconfig_path=ns.kubeconfig_path,
        model=ns.model,
        timeout_s=ns.timeout_sec,
    )

    if result.reasoning:
        # Mirror the reasoning to stderr so it lands in the stress worker's
        # log file; --output is the authoritative machine-readable artifact.
        print(f"[fault_verifier] reasoning: {result.reasoning}", file=sys.stderr)
    if result.parse_error:
        print(f"[fault_verifier] parse_error: {result.parse_error}", file=sys.stderr)

    with open(ns.output, "w") as f:
        json.dump(dataclasses.asdict(result), f, indent=2, default=str)

    return 0


if __name__ == "__main__":
    sys.exit(main())
