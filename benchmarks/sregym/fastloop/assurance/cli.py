"""No-LLM assurance suite CLI: ``uv run python -m benchmarks.sregym.fastloop.assurance <command>``.

    seed   rebuild the lifecycle seed repository (SREGym source + checked-in .sdo)
    run    against a warm ``fastloop up`` lane: every single fault, compositions,
           repeated iterations, and an optional healthy soak; writes results JSON
    table  print a results file as the per-fault latency table

Example::

    uv run python -m benchmarks.sregym.fastloop.assurance seed --out ../assurance-runs/seed-hotel
    uv run python -m benchmarks.sregym.fastloop up --run-dir ../assurance-runs/s0 \\
        --seed ../assurance-runs/seed-hotel --cluster-prefix assure-s --worker-id 0
    uv run python -m benchmarks.sregym.fastloop.assurance run --run-dir ../assurance-runs/s0 --iterations 5

No LLM or Codex call is made: the responder is scripted and the broker has no reflector.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path

from benchmarks.sregym.fastloop.assurance.catalog import COMPOSITES, SINGLE_FAULTS, composite, single_fault
from benchmarks.sregym.fastloop.assurance.harness import (
    STATE_CONFIGMAP,
    ControllerProcess,
    ControllerSettings,
    HarnessError,
    Kubectl,
    Layout,
    PortForward,
    build_binaries,
    deploy_prober,
    utcnow,
)
from benchmarks.sregym.fastloop.assurance.results import Check, FaultRun, StrayIncident, SuiteResults
from benchmarks.sregym.fastloop.assurance.seed import build_seed_repository
from benchmarks.sregym.fastloop.assurance.soak import run_soak
from benchmarks.sregym.fastloop.assurance.suite import AssuranceSuite, Bounds
from benchmarks.sregym.fastloop.environment import FastloopEnvironment
from benchmarks.sregym.fastloop.fault_driver import SregymFaultDriver
from benchmarks.sregym.fastloop.worker_client import SregymWorker, worker_argv

logger = logging.getLogger(__name__)

DEPLOY_OP_TIMEOUT_SECONDS = 2400.0
#: ``run.go`` bounds the state tracker's start by this; the first fault must come later (the frozen-diff bug).
STATE_START_DEADLINE_SECONDS = 30.0


def _seed(args: argparse.Namespace) -> int:
    commit = build_seed_repository(args.out.resolve(), sregym_dir=args.sregym_dir.resolve())
    print(f"seed repository {args.out} at {commit}")
    return 0


def _git_head(repository: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _hold(policy: str, iteration: int) -> bool:
    return policy == "all" or (policy == "first" and iteration == 1)


def _run(args: argparse.Namespace) -> int:
    environment = FastloopEnvironment.load(args.run_dir.resolve())
    os.environ["KUBECONFIG"] = str(environment.kubeconfig)
    layout = Layout(environment.run_dir / "assurance").create()
    kubectl = Kubectl(environment.kubeconfig)
    namespace = environment.namespace
    control_namespace = f"{namespace}-sdo"
    faults = [single_fault(name) for name in args.fault] if args.fault else list(SINGLE_FAULTS)
    if args.no_single:
        faults = []
    composites = [composite(name) for name in args.composite] if args.composite else []
    if args.all_composites:
        composites = list(COMPOSITES)
    run_id = args.run_id or f"{utcnow():%Y%m%dT%H%M%SZ}"
    results_path = args.results or (layout.root / "results" / f"{run_id}.json")

    binaries = build_binaries(environment.workspace, layout.bin_dir)
    digest = deploy_prober(
        kubectl,
        namespace=control_namespace,
        app_namespace=namespace,
        image=environment.images.controller,
        binary=binaries.prober,
    )
    # A fresh controller: no restored incident, lease, or baseline from an earlier run.
    kubectl.run("delete", "configmap", STATE_CONFIGMAP, "--ignore-not-found=true", namespace=control_namespace)
    kubectl.run("delete", "lease", "sdo-controller", "--ignore-not-found=true", namespace=control_namespace)
    results = SuiteResults(
        run_id=run_id,
        cluster=environment.cluster,
        seed_commit=_git_head(environment.workspace),
        controller_binary_sha256=hashlib.sha256(binaries.controller.read_bytes()).hexdigest(),
        prober_digest=digest,
        started_at=utcnow(),
    )
    port_forward = PortForward(kubectl, namespace=control_namespace, log_path=layout.logs / "port-forward.log").start()
    worker = SregymWorker(
        worker_argv(environment.sregym_dir, private_tmp=environment.private_tmp),
        env=environment.worker_process_env(),
        log_path=environment.run_dir / "worker.log",
    )
    controller = ControllerProcess(
        ControllerSettings(
            binary=binaries.controller,
            app_root=environment.workspace,
            namespace=namespace,
            control_namespace=control_namespace,
            application=namespace,
            prober_url=port_forward.url,
            spool=layout.spool,
            worktrees=layout.worktrees,
            logs=layout.logs,
            kubeconfig=environment.kubeconfig,
        )
    )
    exit_code = 0
    try:
        with worker:
            worker.request(
                "deploy",
                problem_id=environment.deploy_problem,
                baseline_path=str(environment.baseline_path),
                timeout=DEPLOY_OP_TIMEOUT_SECONDS,
            )
            driver = SregymFaultDriver(worker, namespace=namespace, health_timeout_seconds=600)
            controller.start()
            warm = controller.wait_for(lambda record: "synthetic_traffic_warm" in record, timeout=180)
            state = controller.wait_for(lambda record: "state_baseline_startup_ms" in record, timeout=180)
            results.notes.append(
                f"controller warm={warm.record.get('synthetic_traffic_warm')} "
                f"startup_ms={warm.record.get('synthetic_traffic_startup_ms')}; "
                f"state baseline startup_ms={state.record.get('state_baseline_startup_ms')} "
                f"unobserved={state.record.get('state_baseline_unobserved_kinds', [])}"
            )
            suite = AssuranceSuite(
                kubectl=kubectl,
                driver=driver,
                controller=controller,
                namespace=namespace,
                control_namespace=control_namespace,
                app_root=environment.workspace,
                spool=layout.spool,
                prober_url=port_forward.url,
                helper_image=environment.images.controller,
                bounds=Bounds(),
            )
            for iteration in range(1, args.iterations + 1):
                for case in faults:
                    run = suite.run_single(case, iteration=iteration, hold=_hold(args.hold, iteration))
                    _record(results, run, controller, results_path, suite.stray_incidents)
                for case in composites:
                    run = suite.run_composite(case, iteration=iteration)
                    _record(results, run, controller, results_path, suite.stray_incidents)
            if args.soak_minutes > 0:
                results.soak = run_soak(
                    suite,
                    controller,
                    kubectl,
                    port_forward,
                    node=f"{environment.cluster}-worker",
                    minutes=args.soak_minutes,
                )
                results.save(results_path)
                # Nothing accumulated in the diff during the soak: the next fault's diff names only its object.
                run = suite.run_single(single_fault("selector-mismatch"), iteration=0)
                run.case = "post-soak selector-mismatch"
                _record(results, run, controller, results_path, suite.stray_incidents)
    except HarnessError as exc:
        logger.error("assurance run aborted: %s", exc)
        results.notes.append(f"aborted: {exc}")
        exit_code = 2
    finally:
        controller.stop()
        port_forward.stop()
        results.port_forward_restarts = port_forward.restarts
        results.finished_at = utcnow()
        results.save(results_path)
    print(format_table(results))
    print(f"stray incidents: {len(results.stray_incidents)}")
    print(f"results: {results_path}")
    failed = [run for run in results.runs if not run.passed]
    if results.soak is not None and not all(check.passed for check in results.soak.checks):
        exit_code = exit_code or 1
    return exit_code or (1 if failed else 0)


def _record(
    results: SuiteResults,
    run: FaultRun,
    controller: ControllerProcess,
    path: Path,
    strays: list[StrayIncident],
) -> None:
    for stray in strays[len(results.stray_incidents) :]:
        print(f"[STRAY] {stray.incident_id} after {stray.after_case}: {stray.findings}", flush=True)
    results.stray_incidents = list(strays)
    if controller.started_at is not None and run.injection_started_at is not None and not results.runs:
        uptime = (run.injection_started_at - controller.started_at).total_seconds()
        run.checks.append(
            Check(
                name="first fault came after the state tracker's start deadline",
                passed=uptime > STATE_START_DEADLINE_SECONDS,
                detail=f"controller up {uptime:.0f}s at the first injection",
            )
        )
    results.runs.append(run)
    results.save(path)
    verdict = "PASS" if run.passed else "FAIL"
    failed = [check.name for check in run.checks if not check.passed and not check.known_gap]
    print(f"[{verdict}] {run.case} #{run.iteration} {run.error or ''} {failed or ''}", flush=True)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def format_table(results: SuiteResults) -> str:
    header = (
        "case",
        "#",
        "detect_s",
        "traffic_s",
        "diff",
        "wrong_fix",
        "partial",
        "clear_s",
        "verify_s",
        "helpers",
        "ok",
    )
    rows = [header]
    for run in results.runs:
        missing = [] if run.diff is None else sorted(set(run.diff.missing) - set(run.live_named))
        diff = (
            "-"
            if run.diff is None
            else (
                ("exact" if not run.live_named else f"exact+live({','.join(run.live_named)})")
                if not (missing or run.diff.unexpected or run.diff.decoys_named)
                else f"miss={missing} extra={run.diff.unexpected} decoy={run.diff.decoys_named}"
            )
        )
        wrong = "/".join(
            "refused" if item.probe.status_exit == 1 and item.probe.gate_exit == 4 else "LEAK"
            for item in run.wrong_fixes
        )
        partial = "/".join(
            "refused" if item.probe.status_exit == 1 and item.probe.gate_exit == 4 else "LEAK"
            for item in run.partial_fixes
        )
        helpers = (
            "-" if run.helpers is None else f"{len(run.helpers.remaining)} left {_fmt(run.helpers.cleanup_seconds)}s"
        )
        rows.append(
            (
                run.case,
                str(run.iteration),
                _fmt(run.incident_opened_seconds),
                _fmt(run.first_finding_seconds.get("traffic-health")),
                diff,
                wrong or "-",
                partial or "-",
                _fmt(run.clear_seconds),
                _fmt(run.verified_seconds),
                helpers,
                "yes" if run.passed else f"NO {run.error or ''}",
            )
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(header))]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) for row in rows)


def _table(args: argparse.Namespace) -> int:
    results = SuiteResults.model_validate_json(args.results.read_text(encoding="utf-8"))
    print(format_table(results))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assurance", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    seed = commands.add_parser("seed", help="rebuild the lifecycle seed repository")
    seed.add_argument("--out", type=Path, required=True)
    seed.add_argument("--sregym-dir", type=Path, default=Path(__file__).resolve().parents[4] / "third_party" / "sregym")
    seed.set_defaults(handler=_seed)

    run = commands.add_parser("run", help="run the suite against a warm fastloop lane")
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--fault", action="append", help="single fault case name (repeat); default: all")
    run.add_argument("--no-single", action="store_true", help="skip the single faults")
    run.add_argument("--composite", action="append", help="composite case name (repeat)")
    run.add_argument("--all-composites", action="store_true")
    run.add_argument("--iterations", type=int, default=1)
    run.add_argument(
        "--hold",
        choices=("none", "first", "all"),
        default="first",
        help="hold each fault past the baseline settle period, then re-inject it (default: first iteration)",
    )
    run.add_argument("--soak-minutes", type=float, default=0.0)
    run.add_argument("--results", type=Path, default=None)
    run.add_argument("--run-id", default=None)
    run.set_defaults(handler=_run)

    table = commands.add_parser("table", help="print a results file as a table")
    table.add_argument("results", type=Path)
    table.set_defaults(handler=_table)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    args = build_parser().parse_args(argv)
    if getattr(args, "iterations", 1) < 1:
        raise SystemExit("--iterations must be positive")
    return int(args.handler(args))
