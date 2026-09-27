"""Command-line entry point for the Self-Defining Operator."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from sdo.operation import OperationConfig, OperationError, check_detector_workspace, operate

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sdo.operational_memory import SandboxRunner


class OperationRunner(Protocol):
    def __call__(self, config: OperationConfig) -> object: ...


def main(
    argv: Sequence[str] | None = None,
    *,
    operation_runner: OperationRunner = operate,
    detector_check_runner: SandboxRunner | None = None,
) -> int:
    """Run a public SDO operation or isolated detector authoring check."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "detector":
        return _check_detector(detector_check_runner)
    try:
        config = _operation_config(args)
        operation_runner(config)
    except (OperationError, OSError, TypeError, ValueError) as exc:
        print(f"sdo operate failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdo",
        description="Deploy and continuously operate an application from source.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    operate_parser = subparsers.add_parser(
        "operate",
        help="deploy, independently verify, and continuously operate an application",
    )
    operate_parser.add_argument("repository", type=Path, help="application Git repository")
    operate_parser.add_argument("--namespace", required=True, help="Kubernetes namespace")
    goal = operate_parser.add_mutually_exclusive_group(required=True)
    goal.add_argument("--goal", help="human-owned health objective")
    goal.add_argument("--goal-file", type=Path, help="file containing the human-owned health objective")
    operate_parser.add_argument("--application", help="application name; defaults to the repository directory name")
    operate_parser.add_argument("--model", default=os.getenv("SDO_MODEL", "gpt-5.4"))
    operate_parser.add_argument("--controller-image", default="sdo-controller:v0.1.0")
    operate_parser.add_argument("--responder-image", default="sdo-responder:v0.1.0")
    operate_parser.add_argument("--validator-image", default="sdo-detector-validator:v0.1.0")
    operate_parser.add_argument("--repository-pvc", default="sdo-application-repository")
    operate_parser.add_argument("--credentials-secret", default="sdo-codex-credentials")
    operate_parser.add_argument("--attempts", type=int, default=3)
    operate_parser.add_argument("--timeout-seconds", type=int, default=1800)
    operate_parser.add_argument("--repair-policy", choices=("commit", "recorded-actions"), default="commit")
    operate_parser.add_argument("--agent-provider", choices=("codex", "claude"), default="codex")
    detector_parser = subparsers.add_parser(
        "detector",
        help="work with detector drafts in the current isolated checkout",
    )
    detector_commands = detector_parser.add_subparsers(dest="detector_command", required=True)
    detector_commands.add_parser(
        "check",
        help="compile and test this checkout using the locked-down validator",
    )
    return parser


def _check_detector(runner: SandboxRunner | None) -> int:
    result = check_detector_workspace(Path.cwd().resolve(), validator=runner)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    return result.returncode


def _operation_config(args: argparse.Namespace) -> OperationConfig:
    repository = args.repository.resolve()
    if args.goal is not None:
        health_objective = args.goal
    else:
        health_objective = args.goal_file.read_text(encoding="utf-8")
    return OperationConfig(
        repository=repository,
        namespace=args.namespace,
        application=args.application or repository.name,
        health_objective=health_objective,
        model=args.model,
        controller_image=args.controller_image,
        responder_image=args.responder_image,
        validator_image=args.validator_image,
        repository_pvc=args.repository_pvc,
        credentials_secret=args.credentials_secret,
        max_attempts=args.attempts,
        timeout_seconds=args.timeout_seconds,
        repair_policy=args.repair_policy,
        agent_provider=args.agent_provider,
    )


if __name__ == "__main__":
    raise SystemExit(main())
