from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from controller.builder.errors import ControllerBuilderError
from controller.builder.go_runner import GoRunner
from controller.builder.paths import find_app_root, find_tool_paths
from controller.builder.workspace import BuildWorkspace, BuildWorkspaceConfig

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
        if args.command == "draft-test":
            return _draft_test(args)
        if args.command == "run-once":
            return _run_once(args)
        if args.command == "evaluate-once":
            return _evaluate_once(args)
        if args.command == "watch":
            return _watch(args)
        if args.command == "controller":
            return _controller(args)
    except ControllerBuilderError as exc:
        print(exc)
        return 1
    except ValueError as exc:
        print(exc)
        return 1
    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdo-detector-check",
        description="Validate app-authored .sdo detector diagnostics without rolling out the controller.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="validate manifest and detector layout")
    _add_common_args(check)

    test = subparsers.add_parser("test", help="validate, generate a temp workspace, and run go test/go build")
    _add_common_args(test)
    test.add_argument("--keep-workdir", action="store_true", help=argparse.SUPPRESS)

    draft_test = subparsers.add_parser(
        "draft-test",
        help="compile and test selected detector drafts without building a controller",
    )
    _add_common_args(draft_test)
    draft_test.add_argument("--detector-id", action="append", required=True)

    run_once = subparsers.add_parser("run-once", help="build and run detectors once against a Kubernetes namespace")
    _add_common_args(run_once)
    run_once.add_argument("--namespace", required=True, help="Kubernetes namespace to observe")
    run_once.add_argument("--keep-workdir", action="store_true", help=argparse.SUPPRESS)

    evaluate_once = subparsers.add_parser(
        "evaluate-once",
        help="route one fresh Kubernetes snapshot through the controller runtime",
    )
    _add_common_args(evaluate_once)
    evaluate_once.add_argument("--namespace", required=True, help="Kubernetes namespace to observe")
    evaluate_once.add_argument("--keep-workdir", action="store_true", help=argparse.SUPPRESS)

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
    watch.add_argument(
        "--dispatcher",
        default=sys.executable,
        help="incident responder executable (default: current Python interpreter)",
    )
    watch.add_argument(
        "--dispatcher-arg",
        action="append",
        default=["-m", "sdo.agent_runtime.responder.codex"],
        help="incident responder argument; may be repeated",
    )
    watch.add_argument("--keep-workdir", action="store_true", help=argparse.SUPPRESS)

    controller = subparsers.add_parser(
        "controller",
        help="build generated diagnostics once and run the durable Kubernetes Job-mode controller",
    )
    _add_common_args(controller)
    controller.add_argument("--namespace", required=True)
    controller.add_argument("--application")
    controller.add_argument("--source-commit")
    controller.add_argument("--deployed-commit")
    controller.add_argument("--responder-image", required=True)
    controller.add_argument("--repository-pvc", required=True)
    controller.add_argument("--repository-mount-path", default="/workspace")
    controller.add_argument("--repository-pvc-subpath", default="")
    controller.add_argument("--credentials-secret", required=True)
    controller.add_argument("--worktree-root", type=Path, required=True)
    controller.add_argument("--responder-command", default=sys.executable)
    controller.add_argument("--responder-arg", action="append", default=[])
    controller.add_argument("--responder-env", action="append", default=[])
    controller.add_argument("--broker-command", default=sys.executable)
    controller.add_argument("--broker-arg", action="append", default=[])
    controller.add_argument("--response-timeout", default="30m")
    controller.add_argument("--verification-timeout", default="2m")
    controller.add_argument("--repair-policy", choices=("commit", "recorded-actions"), default="commit")
    controller.add_argument("--duration", default="")
    controller.add_argument("--lease-name", default="sdo-controller")
    controller.add_argument("--exit-after-closure", action="store_true")
    controller.add_argument("--keep-workdir", action="store_true", help=argparse.SUPPRESS)
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
        BuildWorkspaceConfig(
            app_root=app_root,
            sdk_dir=tool_paths.sdk_dir,
            core_dir=tool_paths.core_dir,
            runtime_dir=tool_paths.runtime_dir,
            keep=False,
        )
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
            runtime_dir=tool_paths.runtime_dir,
            keep=args.keep_workdir,
        )
    ) as workspace:
        for command in [["mod", "tidy"], ["test", "./..."], ["build", "-buildvcs=false", "./cmd/controller"]]:
            exit_code = runner.run(command, cwd=workspace.path)
            if exit_code != 0:
                return exit_code
        if args.keep_workdir:
            print(f"kept controller build workspace: {workspace.path}")
    return 0


def _draft_test(args: argparse.Namespace) -> int:
    app_root = _app_root(args)
    tool_paths = find_tool_paths()
    runner = GoRunner.from_environment()
    with BuildWorkspace.create(
        BuildWorkspaceConfig(
            app_root=app_root,
            sdk_dir=tool_paths.sdk_dir,
            core_dir=tool_paths.core_dir,
            runtime_dir=tool_paths.runtime_dir,
            detector_ids=tuple(args.detector_id),
        )
    ) as workspace:
        packages = ["./" + detector.package.removeprefix("./") for detector in workspace.manifest.detectors]
        for command in [["mod", "tidy"], ["test", *packages, "./generated"]]:
            exit_code = runner.run(command, cwd=workspace.path)
            if exit_code != 0:
                return exit_code
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
            runtime_dir=tool_paths.runtime_dir,
            keep=args.keep_workdir,
        )
    ) as workspace:
        for command in [
            ["mod", "tidy"],
            [
                "run",
                "-buildvcs=false",
                "./cmd/controller",
                "--run-once",
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
            print(f"kept controller build workspace: {workspace.path}")
    return 0


def _evaluate_once(args: argparse.Namespace) -> int:
    app_root = _app_root(args)
    tool_paths = find_tool_paths()
    runner = GoRunner.from_environment()
    with BuildWorkspace.create(
        BuildWorkspaceConfig(
            app_root=app_root,
            sdk_dir=tool_paths.sdk_dir,
            core_dir=tool_paths.core_dir,
            runtime_dir=tool_paths.runtime_dir,
            keep=args.keep_workdir,
        )
    ) as workspace:
        for command in [
            ["mod", "tidy"],
            [
                "run",
                "-buildvcs=false",
                "./cmd/controller",
                "--evaluate-once",
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
            print(f"kept controller build workspace: {workspace.path}", file=sys.stderr)
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
            runtime_dir=tool_paths.runtime_dir,
            keep=args.keep_workdir,
        )
    ) as workspace:
        # Build the detector binary exactly once; the failure modes we care
        # about (CrashLoopBackOff, OOMKilled, FailedScheduling) only mature
        # seconds-to-minutes after fault injection, so we re-run the same
        # compiled binary across the window rather than rebuilding each time.
        binary = workspace.path / "controller-bin"
        for command in [
            ["mod", "tidy"],
            ["build", "-buildvcs=false", "-o", str(binary), "./cmd/controller"],
        ]:
            exit_code = runner.run(command, cwd=workspace.path)
            if exit_code != 0:
                return exit_code

        duration_s = max(1, (args.iterations - 1) * args.interval_s + 1)
        command = [
            str(binary),
            "--namespace",
            args.namespace,
            "--app-root",
            str(app_root),
            "--dispatcher",
            args.dispatcher,
            "--dispatcher-mode",
            "local",
        ]
        for dispatcher_arg in args.dispatcher_arg:
            command.extend(["--dispatcher-arg", dispatcher_arg])
        command.extend(["--duration", f"{duration_s}s"])
        completed = subprocess.run(
            command,
            cwd=workspace.path,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.returncode != 0:
            return completed.returncode

        if args.keep_workdir:
            print(f"kept controller build workspace: {workspace.path}", file=sys.stderr)
    return 0


def _controller(args: argparse.Namespace) -> int:
    app_root = _app_root(args)
    worktree_root = args.worktree_root.resolve()
    if not getattr(args, "controller_update_rollout", False):
        current_head = _git_head(app_root)
        pending = (
            None if current_head == "unknown" else _pending_controller_rollout(app_root, worktree_root, current_head)
        )
        if pending is not None:
            return _execute_controller_update_rollout(args, app_root, worktree_root, pending)
    tool_paths = find_tool_paths()
    runner = GoRunner.from_environment()
    initial_diagnostics = _diagnostics_fingerprint(app_root)
    with BuildWorkspace.create(
        BuildWorkspaceConfig(
            app_root=app_root,
            sdk_dir=tool_paths.sdk_dir,
            core_dir=tool_paths.core_dir,
            runtime_dir=tool_paths.runtime_dir,
            keep=args.keep_workdir,
        )
    ) as workspace:
        binary = workspace.path / "sdo-controller"
        for command in [
            ["mod", "tidy"],
            ["build", "-buildvcs=false", "-o", str(binary), "./cmd/controller"],
        ]:
            exit_code = runner.run(command, cwd=workspace.path)
            if exit_code != 0:
                return exit_code

        responder_args = args.responder_arg or ["-m", "sdo.agent_runtime.responder.job"]
        broker_args = args.broker_arg or [
            "-m",
            "sdo.agent_runtime.responder.broker_cli",
            "--proposal-command",
            "git diff --check HEAD --",
        ]
        command = [
            str(binary),
            "--namespace",
            args.namespace,
            "--app-root",
            str(app_root),
            "--dispatcher-mode",
            "job",
            "--dispatcher",
            args.responder_command,
        ]
        for responder_arg in responder_args:
            command.extend(["--dispatcher-arg", responder_arg])
        for environment in args.responder_env:
            command.extend(["--responder-env", environment])
        command.extend(
            [
                "--responder-image",
                args.responder_image,
                "--repository-pvc",
                args.repository_pvc,
                "--repository-mount-path",
                args.repository_mount_path,
                "--responder-credentials-secret",
                args.credentials_secret,
                "--broker",
                args.broker_command,
            ]
        )
        for broker_arg in broker_args:
            command.extend(["--broker-arg", broker_arg])
        command.extend(
            [
                "--broker-worktree-root",
                str(worktree_root),
                "--response-timeout",
                args.response_timeout,
                "--verification-timeout",
                args.verification_timeout,
                "--repair-policy",
                args.repair_policy,
                "--lease-name",
                args.lease_name,
            ]
        )
        optional_values = {
            "--application": args.application,
            "--source-commit": args.source_commit,
            "--deployed-commit": args.deployed_commit,
            "--repository-pvc-subpath": args.repository_pvc_subpath,
            "--duration": args.duration,
        }
        if args.exit_after_closure:
            command.append("--exit-after-closure")
        for flag, value in optional_values.items():
            if value:
                command.extend([flag, value])
        completed = subprocess.run(command, cwd=workspace.path, check=False)
        if args.keep_workdir:
            print(f"kept controller build workspace: {workspace.path}", file=sys.stderr)
        returncode = completed.returncode
    if returncode != 0 or getattr(args, "controller_update_rollout", False):
        return returncode
    accepted_diagnostics = _diagnostics_fingerprint(app_root)
    current_head = _git_head(app_root)
    pending = None if current_head == "unknown" else _pending_controller_rollout(app_root, worktree_root, current_head)
    if pending is None:
        if accepted_diagnostics != initial_diagnostics:
            raise ValueError("accepted diagnostics changed without a correlated durable rollout expectation")
        return returncode
    return _execute_controller_update_rollout(args, app_root, worktree_root, pending)


def _execute_controller_update_rollout(
    args: argparse.Namespace,
    app_root: Path,
    worktree_root: Path,
    expectation: dict[str, str],
) -> int:
    rollout_args = copy.copy(args)
    rollout_args.exit_after_closure = False
    # The rollout performs cache sync, one evaluation, and a durable state
    # write. Keep it bounded, but allow normal API latency under validator and
    # controller handoff load.
    rollout_args.duration = "30s"
    rollout_args.source_commit = None
    rollout_args.controller_update_rollout = True
    started_at = datetime.now(timezone.utc)
    rollout_returncode = _controller(rollout_args)
    completed_at = datetime.now(timezone.utc)
    controller_job = os.environ.get("SDO_CONTROLLER_JOB", "").strip()
    controller_pod_uid = os.environ.get("SDO_CONTROLLER_POD_UID", "").strip()
    if not controller_job or not controller_pod_uid:
        raise ValueError("durable controller rollout requires SDO_CONTROLLER_JOB and SDO_CONTROLLER_POD_UID")
    record: dict[str, object] = {
        "schema_version": "sdo.controller-rollout/v1",
        "incident_id": expectation["incident_id"],
        "reflection_commit": expectation["reflection_commit"],
        "before_detector_fingerprint": expectation["before_detector_fingerprint"],
        "after_detector_fingerprint": expectation["after_detector_fingerprint"],
        "controller_job": controller_job,
        "controller_pod_uid": controller_pod_uid,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "returncode": rollout_returncode,
        "success": rollout_returncode == 0,
    }
    _persist_controller_rollout(app_root, worktree_root, record)
    print(
        json.dumps(
            {
                "controller_update_rollout": expectation["after_detector_fingerprint"],
                "incident_id": expectation["incident_id"],
                "reflection_commit": expectation["reflection_commit"],
                "controller_job": controller_job,
                "controller_pod_uid": controller_pod_uid,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "returncode": rollout_returncode,
                "source_commit": expectation["reflection_commit"],
            }
        )
    )
    return rollout_returncode


def _pending_controller_rollout(
    app_root: Path,
    worktree_root: Path,
    reflection_commit: str,
) -> dict[str, str] | None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sdo.operational_memory.rollout_cli",
            "pending",
            "--repository",
            str(app_root),
            "--worktree-root",
            str(worktree_root),
            "--reflection-commit",
            reflection_commit,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"cannot resolve durable controller rollout: {details}")
    payload = json.loads(completed.stdout)
    if payload is None:
        return None
    required = {
        "incident_id",
        "reflection_commit",
        "before_detector_fingerprint",
        "after_detector_fingerprint",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or not all(isinstance(payload[field], str) and payload[field] for field in required)
    ):
        raise ValueError("durable controller rollout command returned an invalid expectation")
    return payload


def _persist_controller_rollout(app_root: Path, worktree_root: Path, record: dict[str, object]) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sdo.operational_memory.rollout_cli",
            "record",
            "--repository",
            str(app_root),
            "--worktree-root",
            str(worktree_root),
        ],
        input=json.dumps(record),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"cannot persist durable controller rollout: {details}")


def _diagnostics_fingerprint(app_root: Path) -> str:
    root = app_root.resolve()
    diagnostics = root / ".sdo" / "diagnostics"
    digest = hashlib.sha256()
    for path in sorted(diagnostics.rglob("*")):
        relative = path.relative_to(diagnostics).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(path.readlink().as_posix().encode())
        elif path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_head(app_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(app_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


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
