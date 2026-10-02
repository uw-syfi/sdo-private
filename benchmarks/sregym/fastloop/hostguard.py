"""Host-load governor and stall watchdog for experiment runs.

Other jobs on a shared host spike the load average past 40 and turn healthy runs
into ``health`` timeouts and pods that never become Ready. Two small tools keep
those out of the results:

``wait`` holds a cluster-heavy step until the 1-minute load has stayed under a
threshold for a calm window (or a maximum wait runs out, which it reports).

``watch`` reads a run directory's file mtimes, the controller install marker and
the load, and says whether the run is progressing or stalled, whether the stall
looks like infrastructure, a detection gap or an unclassified setup stall, and
what to do about it.

    python -m benchmarks.sregym.fastloop.hostguard wait --max-load 20
    python -m benchmarks.sregym.fastloop.hostguard watch --path /mnt/data/shli/clc-runs/mini-a
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

CONTROLLER_STATE_FILENAME = "sdo_persistent_controller.json"
_SKIPPED_DIRECTORIES = frozenset({".git", "vendor", "node_modules"})

StallKind = Literal["progressing", "infra", "product", "unknown"]


@dataclass(frozen=True)
class LoadPolicy:
    max_load: float = 20.0
    #: How long the 1-minute load must stay at or under ``max_load`` before the step may start.
    calm_seconds: float = 180.0
    max_wait_seconds: float = 1800.0
    poll_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.max_load <= 0:
            raise ValueError(f"max_load must be positive, got {self.max_load}")
        if self.calm_seconds < 0:
            raise ValueError(f"calm_seconds must not be negative, got {self.calm_seconds}")
        if self.max_wait_seconds < 0:
            raise ValueError(f"max_wait_seconds must not be negative, got {self.max_wait_seconds}")
        if self.poll_seconds <= 0:
            raise ValueError(f"poll_seconds must be positive, got {self.poll_seconds}")


@dataclass(frozen=True)
class LoadWait:
    calm: bool
    waited_seconds: float
    final_load: float
    peak_load: float


def wait_for_calm_load(
    policy: LoadPolicy,
    read_load: Callable[[], float],
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> LoadWait:
    """Block until the load has been at or under the threshold for the calm window, or the wait runs out."""

    started = monotonic()
    calm_since: float | None = None
    peak = 0.0
    while True:
        load = read_load()
        peak = max(peak, load)
        now = monotonic()
        if load <= policy.max_load:
            calm_since = now if calm_since is None else calm_since
            if now - calm_since >= policy.calm_seconds:
                return LoadWait(calm=True, waited_seconds=now - started, final_load=load, peak_load=peak)
        else:
            calm_since = None
        if now - started >= policy.max_wait_seconds:
            return LoadWait(calm=False, waited_seconds=now - started, final_load=load, peak_load=peak)
        sleep(policy.poll_seconds)


@dataclass(frozen=True)
class StallPolicy:
    #: No file under the watched paths written for this long counts as a stall.
    stall_seconds: float = 600.0
    #: A stall at or over this 1-minute load is blamed on the host.
    heavy_load: float = 25.0

    def __post_init__(self) -> None:
        if self.stall_seconds <= 0:
            raise ValueError(f"stall_seconds must be positive, got {self.stall_seconds}")
        if self.heavy_load <= 0:
            raise ValueError(f"heavy_load must be positive, got {self.heavy_load}")


@dataclass(frozen=True)
class StallSample:
    #: Age of the newest file under the watched paths; ``None`` when there is nothing to watch.
    newest_file_age_seconds: float | None
    controller_installed: bool
    seconds_since_controller_install: float | None
    load: float


@dataclass(frozen=True)
class StallVerdict:
    kind: StallKind
    reason: str
    action: str


def classify_stall(sample: StallSample, policy: StallPolicy) -> StallVerdict:
    age = sample.newest_file_age_seconds
    if age is None:
        return StallVerdict(
            "unknown",
            "no files found under the watched paths, so there is no evidence of progress",
            "check the --path values; a run that never wrote a file did not start",
        )
    if age < policy.stall_seconds:
        return StallVerdict("progressing", f"a file was written {age:.0f}s ago", "none")
    if sample.load >= policy.heavy_load:
        return StallVerdict(
            "infra",
            f"no file written for {age:.0f}s while the host load is {sample.load:.0f}",
            "wait for load under 20 (hostguard wait), then retry once on the same cluster; "
            "exclude this attempt from results as an infrastructure failure",
        )
    since_install = sample.seconds_since_controller_install
    if sample.controller_installed and since_install is not None and since_install >= policy.stall_seconds:
        return StallVerdict(
            "product",
            f"the controller was installed {since_install:.0f}s ago and nothing has happened since: "
            "a detection gap (no incident opened), not a hang",
            "inspect the controller's findings and detectors; set --detection-timeout so an undetected "
            "incident ends as 'undetected' and the fault is recovered",
        )
    return StallVerdict(
        "unknown",
        f"no file written for {age:.0f}s before the controller was installed: a cluster setup or lifecycle stall",
        "check the cluster (pods Ready, image load) and the lifecycle session log; retry once on a fresh cluster "
        "if nothing is moving",
    )


def _walk_files(root: Path):
    if root.is_file():
        yield root
        return
    for directory, directories, filenames in os.walk(root):
        directories[:] = [name for name in directories if name not in _SKIPPED_DIRECTORIES]
        for filename in filenames:
            yield Path(directory) / filename


def sample_progress(paths: Sequence[Path], *, now: float, load: float) -> StallSample:
    newest: float | None = None
    controller_mtime: float | None = None
    for root in paths:
        for path in _walk_files(root):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            newest = mtime if newest is None else max(newest, mtime)
            if path.name == CONTROLLER_STATE_FILENAME:
                controller_mtime = mtime if controller_mtime is None else max(controller_mtime, mtime)
    return StallSample(
        newest_file_age_seconds=None if newest is None else max(0.0, now - newest),
        controller_installed=controller_mtime is not None,
        seconds_since_controller_install=None if controller_mtime is None else max(0.0, now - controller_mtime),
        load=load,
    )


_EXIT_CODES: dict[StallKind, int] = {"progressing": 0, "infra": 1, "product": 2, "unknown": 3}


def _wait(args: argparse.Namespace) -> int:
    policy = LoadPolicy(
        max_load=args.max_load,
        calm_seconds=args.calm_seconds,
        max_wait_seconds=args.max_wait_seconds,
        poll_seconds=args.poll_seconds,
    )
    result = wait_for_calm_load(policy, lambda: os.getloadavg()[0], time.sleep, time.monotonic)
    print(json.dumps(asdict(result)))
    if not result.calm:
        print(
            f"host load stayed above {policy.max_load:.0f} for {result.waited_seconds:.0f}s "
            f"(peak {result.peak_load:.0f}); results from this step are load-contaminated",
            file=sys.stderr,
        )
    return 0 if result.calm else 3


def _watch(args: argparse.Namespace) -> int:
    policy = StallPolicy(stall_seconds=args.stall_seconds, heavy_load=args.heavy_load)
    paths = [Path(path) for path in args.path]
    while True:
        sample = sample_progress(paths, now=time.time(), load=os.getloadavg()[0])
        verdict = classify_stall(sample, policy)
        print(json.dumps({**asdict(verdict), "sample": asdict(sample)}), flush=True)
        if verdict.kind != "progressing" or args.interval is None:
            return _EXIT_CODES[verdict.kind]
        time.sleep(args.interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hostguard", description="Host-load governor and stall watchdog for experiment runs"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    wait = commands.add_parser("wait", help="hold until the host load has been calm for a window")
    wait.add_argument("--max-load", type=float, default=LoadPolicy.max_load)
    wait.add_argument("--calm-seconds", type=float, default=LoadPolicy.calm_seconds)
    wait.add_argument("--max-wait-seconds", type=float, default=LoadPolicy.max_wait_seconds)
    wait.add_argument("--poll-seconds", type=float, default=LoadPolicy.poll_seconds)
    wait.set_defaults(handler=_wait)

    watch = commands.add_parser("watch", help="classify a run as progressing or stalled (exit 0/1/2/3)")
    watch.add_argument("--path", action="append", required=True, help="run directory or file to watch; repeatable")
    watch.add_argument("--stall-seconds", type=float, default=StallPolicy.stall_seconds)
    watch.add_argument("--heavy-load", type=float, default=StallPolicy.heavy_load)
    watch.add_argument("--interval", type=float, default=None, help="keep watching every N seconds until a stall")
    watch.set_defaults(handler=_watch)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
