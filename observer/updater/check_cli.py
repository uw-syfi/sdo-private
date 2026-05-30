from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from observer.updater.errors import ObserverUpdaterError
from observer.updater.go_runner import GoRunner
from observer.updater.paths import find_app_root, find_tool_paths
from observer.updater.workspace import BuildWorkspace, BuildWorkspaceConfig

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return _check(args)
        if args.command == "test":
            return _test(args)
        if args.command == "run-once":
            return _run_once(args)
        if args.command == "watch":
            return _watch(args)
    except ObserverUpdaterError as exc:
        print(exc)
        return 1
    except ValueError as exc:
        print(exc)
        return 1
    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sds-observer-check",
        description="Validate app-authored .sds observer diagnostics without rolling out the observer.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="validate manifest and detector layout")
    _add_common_args(check)

    test = subparsers.add_parser("test", help="validate, generate a temp workspace, and run go test/go build")
    _add_common_args(test)
    test.add_argument("--keep-workdir", action="store_true", help=argparse.SUPPRESS)

    run_once = subparsers.add_parser("run-once", help="build and run detectors once against a Kubernetes namespace")
    _add_common_args(run_once)
    run_once.add_argument("--namespace", required=True, help="Kubernetes namespace to observe")
    run_once.add_argument("--keep-workdir", action="store_true", help=argparse.SUPPRESS)

    watch = subparsers.add_parser(
        "watch",
        help="build detectors once, then sample them over a window of iterations to expose how findings evolve",
    )
    _add_common_args(watch)
    watch.add_argument("--namespace", required=True, help="Kubernetes namespace to observe")
    watch.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="number of detector samples to take across the window (default: 5)",
    )
    watch.add_argument(
        "--interval-s",
        type=int,
        default=30,
        help="seconds to wait between samples (default: 30)",
    )
    watch.add_argument("--keep-workdir", action="store_true", help=argparse.SUPPRESS)
    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--app",
        type=Path,
        default=None,
        help="application root; defaults to discovering from the current working directory",
    )


def _check(args: argparse.Namespace) -> int:
    app_root = _app_root(args)
    tool_paths = find_tool_paths()
    with BuildWorkspace.create(
        BuildWorkspaceConfig(app_root=app_root, sdk_dir=tool_paths.sdk_dir, core_dir=tool_paths.core_dir, keep=False)
    ) as workspace:
        print(f"validated {len(workspace.manifest.detectors)} detector(s)")
    return 0


def _test(args: argparse.Namespace) -> int:
    app_root = _app_root(args)
    tool_paths = find_tool_paths()
    runner = GoRunner.from_environment()
    with BuildWorkspace.create(
        BuildWorkspaceConfig(
            app_root=app_root,
            sdk_dir=tool_paths.sdk_dir,
            core_dir=tool_paths.core_dir,
            keep=args.keep_workdir,
        )
    ) as workspace:
        for command in [["mod", "tidy"], ["test", "./..."], ["build", "-buildvcs=false", "./cmd/observer"]]:
            exit_code = runner.run(command, cwd=workspace.path)
            if exit_code != 0:
                return exit_code
        if args.keep_workdir:
            print(f"kept observer build workspace: {workspace.path}")
    return 0


def _run_once(args: argparse.Namespace) -> int:
    app_root = _app_root(args)
    tool_paths = find_tool_paths()
    runner = GoRunner.from_environment()
    with BuildWorkspace.create(
        BuildWorkspaceConfig(
            app_root=app_root,
            sdk_dir=tool_paths.sdk_dir,
            core_dir=tool_paths.core_dir,
            keep=args.keep_workdir,
        )
    ) as workspace:
        for command in [
            ["mod", "tidy"],
            [
                "run",
                "-buildvcs=false",
                "./cmd/observer",
                "--namespace",
                args.namespace,
                "--app-root",
                str(app_root),
            ],
        ]:
            exit_code = runner.run(command, cwd=workspace.path)
            if exit_code != 0:
                return exit_code
        if args.keep_workdir:
            print(f"kept observer build workspace: {workspace.path}")
    return 0


def _watch(args: argparse.Namespace) -> int:
    if args.iterations < 1:
        raise ValueError("--iterations must be >= 1")
    if args.interval_s < 0:
        raise ValueError("--interval-s must be >= 0")

    app_root = _app_root(args)
    tool_paths = find_tool_paths()
    runner = GoRunner.from_environment()
    with BuildWorkspace.create(
        BuildWorkspaceConfig(
            app_root=app_root,
            sdk_dir=tool_paths.sdk_dir,
            core_dir=tool_paths.core_dir,
            keep=args.keep_workdir,
        )
    ) as workspace:
        # Build the detector binary exactly once; the failure modes we care
        # about (CrashLoopBackOff, OOMKilled, FailedScheduling) only mature
        # seconds-to-minutes after fault injection, so we re-run the same
        # compiled binary across the window rather than rebuilding each time.
        binary = workspace.path / "observer-bin"
        for command in [
            ["mod", "tidy"],
            ["build", "-buildvcs=false", "-o", str(binary), "./cmd/observer"],
        ]:
            exit_code = runner.run(command, cwd=workspace.path)
            if exit_code != 0:
                return exit_code

        for iteration in range(args.iterations):
            if iteration > 0:
                time.sleep(args.interval_s)
            completed = subprocess.run(
                [str(binary), "--namespace", args.namespace, "--app-root", str(app_root)],
                cwd=workspace.path,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.stderr.strip():
                print(completed.stderr.strip(), file=sys.stderr)
            print(
                json.dumps(
                    {
                        "observer_iteration": iteration,
                        "returncode": completed.returncode,
                        "findings": _collect_finding_payloads(completed.stdout),
                    }
                )
            )

        if args.keep_workdir:
            print(f"kept observer build workspace: {workspace.path}", file=sys.stderr)
    return 0


def _collect_finding_payloads(stdout: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("rule_id"):
            payloads.append(payload)
    return payloads


def _app_root(args: argparse.Namespace) -> Path:
    if args.app is not None:
        return args.app.resolve()
    return find_app_root()


if __name__ == "__main__":
    raise SystemExit(main())
