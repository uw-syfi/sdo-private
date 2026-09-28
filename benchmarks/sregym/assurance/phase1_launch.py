"""Launch the phase-1 live assurance matrix (``experiments/assurance/PLAN.md`` (d), (e)).

One command starts the eight phase-1 lanes (``assure-w0``..``assure-w7``),
each bound to its config in ``experiments/assurance/phase1/`` by the lane the
config's own header names (``PLAN.md`` D11). Before anything starts:

- every lane's cluster is verified (never created here: SREGym's own harness
  bootstraps a lane's kind cluster lazily on first use, so "verify" only
  checks that the lane is not already claimed by another process);
- every lane's launch preflight runs in enforcing mode
  (:mod:`benchmarks.sregym.runner.preflight`); **any** failing lane aborts the
  whole matrix before anything is launched;
- the Codex quota gate is read offline (:func:`benchmarks.sregym.runner.preflight.read_quota_snapshot`):
  2026-10 correction (see ``RUNBOOK.md``/``HARNESS_DECISIONS.md``): the start
  gate is budget-aware, not a fixed percent. ``phase1_budget.py`` estimates
  the selected ``--matrix`` preset's (default: PLAN.md's full matrix, no
  stock arm) EXPECTED (nominal) cost in tokens, calibrated from measured
  runs, converted to quota points at a conservative fallback rate; the
  matrix only starts if ``current used_percent + that expected cost <=
  --stop-percent`` (default 96%, one point under PLAN.md (d)'s own 97% hard
  stop), auto-shrinking the matrix's attempt/pipeline counts first if it
  does not fit. A small, non-shrinking smoke budget (1 SDO pipeline + 1
  Codex attempt, 1 problem) is reserved ahead of the matrix in this same
  check. The 2x worst case is still computed and printed for information,
  but no longer gates the start decision (2026-10 gate change): the matrix's
  actual safety nets are the hard stop below and the per-lane 1.5x abort.
  The matrix stops hard, mid-run, at ``--stop-percent`` regardless (the
  "Hard stop" rule the plan attributes to ``run.sh`` ``QUOTA-STOP``).

Once running, lane starts are staggered, host load and disk are sampled every
30 s, quota is logged per completed run, and a lane whose own attributed
quota spend exceeds 1.5x its budgeted share of its arm's PLAN.md (d) row is
aborted on its own (the other lanes are not touched).

Everything that could touch a real cluster or spend real quota is injected
(:class:`ClusterProvider`, :class:`QuotaReader`, :class:`HostMonitor`,
:class:`ProcessRunner`, and the preflight callable), so ``--dry-run`` and the
unit tests never create a kind cluster, spend Codex quota, or start a real
subprocess.

State is written to ``<phase1-dir>/.launch/state.json`` after every
transition, so a killed launcher can be restarted with the same command: a
lane already ``done`` is not re-run, and a lane that was ``running`` when the
launcher died is resumed from its recorded run directory (SREGym's own
``benchmarks.sregym.run`` resume support), not restarted from scratch.

Usage::

    uv run python -m benchmarks.sregym.assurance.phase1_launch [--dry-run]
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from benchmarks.sregym.assurance.phase1_budget import (
    FULL_MATRIX,
    REDUCED_MATRIX,
    TokenBudgetEstimate,
    autoshrink_to_fit,
    full_matrix_budget,
    smoke_budget,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from benchmarks.sregym.runner.preflight import PreflightReport

LANE_HEADER = re.compile(r"lane (assure-w(\d))")
LANE_COUNT = 8
LANE_PREFIX = "assure-w"

#: The eight PLAN.md (d) matrix lanes, grouped by arm (D11). Phase 1 has no
#: stock (no-verify) Codex arm (user decision, 2026-09-28): the sole Codex arm
#: is the default, concise-verify baseline, on w4-w7.
ARM_OF_PREFIX: tuple[tuple[str, str], ...] = (
    ("sdo_codex_luna_assure_p1_", "sdo_codex"),
    ("codex_luna_verify_assure_p1_", "codex_verify"),
)

#: PLAN.md (d) "Phase 1 matrix" row totals (weekly-% of the shared Codex quota,
#: across every lane of that arm) and how many lanes share each row.
ARM_BUDGET_PERCENT: dict[str, float] = {"sdo_codex": 6.2, "codex_verify": 2.7}
ARM_LANE_COUNT: dict[str, int] = {"sdo_codex": 4, "codex_verify": 4}

LaneStatus = Literal["pending", "running", "done", "failed", "aborted_budget", "aborted_matrix_stop"]
MatrixStatus = Literal["pending", "running", "completed", "aborted_preflight", "aborted_quota_start", "stopped_quota"]


def arm_of(config_name: str) -> str:
    for prefix, arm in ARM_OF_PREFIX:
        if config_name.startswith(prefix):
            return arm
    raise ValueError(f"{config_name!r} does not match any known phase-1 arm prefix")


def lane_budget_percent(arm: str) -> float:
    return ARM_BUDGET_PERCENT[arm] / ARM_LANE_COUNT[arm]


def lane_offset(lane: str) -> int:
    match = re.fullmatch(rf"{re.escape(LANE_PREFIX)}(\d+)", lane)
    if not match:
        raise ValueError(f"{lane!r} is not a phase-1 lane name ({LANE_PREFIX}N)")
    return int(match.group(1))


# --------------------------------------------------------------------------- lane binding


@dataclass(frozen=True)
class LaneBinding:
    lane: str
    config: Path
    arm: str
    budget_percent: float

    def __post_init__(self) -> None:
        if not re.fullmatch(rf"{re.escape(LANE_PREFIX)}\d+", self.lane):
            raise ValueError(f"lane must look like {LANE_PREFIX}N, got {self.lane!r}")
        if self.budget_percent <= 0:
            raise ValueError(f"budget_percent must be positive, got {self.budget_percent}")


def parse_lane_from_header(path: Path) -> str:
    """The lane a phase-1 config's header comment names (``PLAN.md`` D11)."""

    header = path.read_text(encoding="utf-8").split("\n\n", 1)[0]
    matches = LANE_HEADER.findall(header)
    if len(matches) != 1:
        raise ValueError(f"{path} header must name exactly one lane (found {[m[0] for m in matches]})")
    return matches[0][0]


def bind_lanes(phase1_dir: Path) -> dict[str, LaneBinding]:
    """Every phase-1 config bound to the lane its own header names.

    Raises ``ValueError`` if the directory does not contain exactly the
    eight expected lanes with no duplicate or missing lane.
    """

    bindings: dict[str, LaneBinding] = {}
    for config in sorted(phase1_dir.glob("*.toml")):
        lane = parse_lane_from_header(config)
        if lane in bindings:
            raise ValueError(f"lane {lane} is bound twice: {bindings[lane].config.name} and {config.name}")
        arm = arm_of(config.name)
        bindings[lane] = LaneBinding(lane=lane, config=config, arm=arm, budget_percent=lane_budget_percent(arm))
    expected = {f"{LANE_PREFIX}{n}" for n in range(LANE_COUNT)}
    if set(bindings) != expected:
        raise ValueError(f"expected lanes {sorted(expected)}, bound {sorted(bindings)}")
    return bindings


# --------------------------------------------------------------------------- quota gate


@dataclass(frozen=True)
class QuotaGate:
    """Quota gates: start/stop thresholds and the per-lane abort rule.

    2026-10 correction: the start gate is no longer a fixed "used_percent <=
    X" threshold (PLAN.md (d)'s original "Phase-1 start: only if <= 50%").
    Per the 2026-10 pivot, it is budget-aware instead: start only if
    ``current used_percent + the selected matrix's worst-case percent`` (see
    ``phase1_budget.py``) clears the stop line. ``stop_percent`` defaults to
    PLAN.md (d)'s own hard stop (97%); the CLI's own default for a live run
    is 96%, one point of margin under the user's hard stop, per the pivot.
    """

    stop_percent: float = 97.0
    lane_abort_multiplier: float = 1.5

    def __post_init__(self) -> None:
        if not 0 < self.stop_percent <= 100:
            raise ValueError(f"stop_percent must be in (0, 100], got {self.stop_percent}")
        if self.lane_abort_multiplier <= 1.0:
            raise ValueError(f"lane_abort_multiplier must be > 1.0, got {self.lane_abort_multiplier}")

    def can_start_matrix(self, used_percent: float | None, planned_percent: float) -> bool:
        """Budget-aware start gate: current + planned cost must clear the stop line.

        2026-10 gate change (``HARNESS_DECISIONS.md``): *planned_percent* is
        the plan's EXPECTED (nominal) cost, not its 2x worst case -- gating
        on the worst case auto-shrunk the full matrix to a single SDO
        pipeline for no measured reason. The live global stop
        (:meth:`must_stop_matrix`) and the per-lane 1.5x abort
        (:meth:`lane_over_budget`) are the run's actual safety nets; the
        worst case is still computed and printed for information.

        Unknown quota does not block (a missing/expired snapshot cannot
        veto a start; preflight's own quota check independently guards
        this).
        """

        if planned_percent < 0:
            raise ValueError(f"planned_percent must be non-negative, got {planned_percent}")
        return used_percent is None or used_percent + planned_percent <= self.stop_percent

    def must_stop_matrix(self, used_percent: float | None) -> bool:
        """ "Hard stop": the runner does not start a run at or above the stop line."""

        return used_percent is not None and used_percent >= self.stop_percent

    def lane_over_budget(self, cumulative_percent: float, budget_percent: float) -> bool:
        """PLAN.md (d) "Per-lane abort: stop a lane whose running total exceeds 1.5x its row above."""

        return cumulative_percent > budget_percent * self.lane_abort_multiplier


# --------------------------------------------------------------------------- injected dependencies


@dataclass(frozen=True)
class ClusterCheck:
    lane: str
    detail: str
    ok: bool = True


class ClusterProvider(Protocol):
    """Verifies (never creates) a lane's kind cluster before launch."""

    def ensure_lane(self, lane: str) -> ClusterCheck: ...


class QuotaReader(Protocol):
    """The account's current weekly Codex window usage, read offline."""

    def used_percent(self) -> float | None: ...


@dataclass(frozen=True)
class HostSample:
    at: float
    load1: float | None
    load5: float | None
    load15: float | None
    disk_free_bytes: dict[str, int]


class HostMonitor(Protocol):
    def sample(self) -> HostSample: ...


class ProcessHandle(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def run_dir(self) -> Path | None:
        """The run/pipeline directory SREGym reported, once known (for resumability)."""
        ...


class ProcessRunner(Protocol):
    def start(self, binding: LaneBinding, *, resume_dir: Path | None, env: Mapping[str, str]) -> ProcessHandle: ...


class PreflightRunner(Protocol):
    def __call__(self, binding: LaneBinding) -> PreflightReport: ...


# --------------------------------------------------------------------------- real implementations


class SystemClusterProvider:
    """Verifies a lane is not already an active kind cluster owned by another prefix; never creates one."""

    def ensure_lane(self, lane: str) -> ClusterCheck:
        try:
            completed = subprocess.run(["kind", "get", "clusters"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            return ClusterCheck(lane, f"kind get clusters failed: {exc}", ok=False)
        if completed.returncode != 0:
            return ClusterCheck(lane, "kind get clusters unavailable; lane will be created by SREGym at launch")
        clusters = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
        if lane in clusters:
            return ClusterCheck(lane, "reused: already exists")
        return ClusterCheck(lane, "will be created by SREGym at launch (1 control plane + 1 worker)")


class SystemQuotaReader:
    def __init__(self, codex_home: Path | None = None) -> None:
        self._codex_home = codex_home or Path.home() / ".codex"

    def used_percent(self) -> float | None:
        from benchmarks.sregym.runner.preflight import read_quota_snapshot

        snapshot = read_quota_snapshot(self._codex_home)
        if snapshot is None:
            return None
        primary = snapshot.windows.get("primary")
        return primary.used_percent if primary is not None else None


class SystemHostMonitor:
    def __init__(self, disk_paths: Mapping[str, Path]) -> None:
        self._disk_paths = dict(disk_paths)

    def sample(self) -> HostSample:
        import shutil

        try:
            load1, load5, load15 = os.getloadavg()
        except OSError:
            load1 = load5 = load15 = None
        free: dict[str, int] = {}
        for name, path in self._disk_paths.items():
            probe = path
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            try:
                free[name] = shutil.disk_usage(probe).free
            except OSError:
                continue
        return HostSample(at=time.time(), load1=load1, load5=load5, load15=load15, disk_free_bytes=free)


#: ``run.py``'s own announcements (``print(f"New pipeline: {pipeline_dir}")`` and
#: the single-experiment and resume equivalents), captured from the lane's redirected
#: stdout so a killed launcher can resume the right directory instead of restarting.
RUN_DIR_ANNOUNCEMENT = re.compile(
    r"^(?:New (?:pipeline|experiment)|Resuming (?:pipeline from|experiment from)): (.+)$", re.MULTILINE
)


def discover_run_dir(log_path: Path) -> Path | None:
    if not log_path.is_file():
        return None
    matches = RUN_DIR_ANNOUNCEMENT.findall(log_path.read_text(encoding="utf-8", errors="replace"))
    return Path(matches[-1].strip()) if matches else None


class SubprocessHandle:
    def __init__(self, process: subprocess.Popen[bytes], log_path: Path) -> None:
        self._process = process
        self._log_path = log_path

    def poll(self) -> int | None:
        return self._process.poll()

    def terminate(self) -> None:
        self._process.terminate()

    def run_dir(self) -> Path | None:
        return discover_run_dir(self._log_path)


class SubprocessProcessRunner:
    """Runs each lane as ``uv run python -m benchmarks.sregym.run <config-or-resume-dir>``."""

    def __init__(self, project_root: Path, log_dir: Path) -> None:
        self._project_root = project_root
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def start(self, binding: LaneBinding, *, resume_dir: Path | None, env: Mapping[str, str]) -> ProcessHandle:
        target = str(resume_dir) if resume_dir is not None else str(binding.config)
        log_path = self._log_dir / f"{binding.lane}.log"
        full_env = dict(os.environ)
        full_env.update(env)
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                ["uv", "run", "python", "-m", "benchmarks.sregym.run", target],
                cwd=str(self._project_root),
                env=full_env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        return SubprocessHandle(process, log_path)


def default_preflight_runner(
    *, project_root: Path, sregym_dir: Path, max_quota_used_percent: float | None = None
) -> PreflightRunner:
    """``max_quota_used_percent`` follows the launcher's own quota gate (2026-10 pivot) instead of
    preflight's hard-coded 85% default; the effective value is always recorded in the report's
    facts, whichever it came from, so the manifest shows what preflight actually enforced."""

    from benchmarks.sregym.runner.preflight import PreflightSettings, SystemHost, load_arm_configs, run_preflight

    settings = PreflightSettings(max_quota_used_percent=max_quota_used_percent) if max_quota_used_percent else None

    def run(binding: LaneBinding) -> PreflightReport:
        env = dict(os.environ)
        env["SREGYM_KIND_CLUSTER_PREFIX"] = LANE_PREFIX
        env["SREGYM_WORKER_ID_OFFSET"] = str(lane_offset(binding.lane))
        configs = load_arm_configs([binding.config], env)
        report = run_preflight(
            configs, project_root=project_root, sregym_dir=sregym_dir, env=env, host=SystemHost(), settings=settings
        )
        report.facts["preflight_max_quota_used_percent"] = (
            settings.max_quota_used_percent if settings is not None else PreflightSettings().max_quota_used_percent
        )
        report.facts["preflight_max_quota_used_percent_source"] = "quota_gate" if settings is not None else "default"
        return report

    return run


# --------------------------------------------------------------------------- state


@dataclass
class LaneRunRecord:
    attempt: int
    started_at: float
    finished_at: float | None
    used_percent_before: float | None
    used_percent_after: float | None
    attributed_delta_percent: float | None
    returncode: int | None


@dataclass
class LaneState:
    binding: LaneBinding
    status: LaneStatus = "pending"
    cumulative_percent: float = 0.0
    run_dir: str | None = None
    records: list[LaneRunRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["binding"] = {**data["binding"], "config": str(self.binding.config)}
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LaneState:
        binding_data = dict(data["binding"])
        binding_data["config"] = Path(binding_data["config"])
        return cls(
            binding=LaneBinding(**binding_data),
            status=data.get("status", "pending"),
            cumulative_percent=data.get("cumulative_percent", 0.0),
            run_dir=data.get("run_dir"),
            records=[LaneRunRecord(**record) for record in data.get("records", [])],
        )


@dataclass
class MatrixState:
    lanes: dict[str, LaneState]
    matrix_status: MatrixStatus = "pending"
    cluster_verified: bool = False
    preflight_done: bool = False
    started_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "matrix_status": self.matrix_status,
            "cluster_verified": self.cluster_verified,
            "preflight_done": self.preflight_done,
            "started_at": self.started_at,
            "lanes": {lane: state.to_dict() for lane, state in self.lanes.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MatrixState:
        return cls(
            lanes={lane: LaneState.from_dict(value) for lane, value in data["lanes"].items()},
            matrix_status=data.get("matrix_status", "pending"),
            cluster_verified=data.get("cluster_verified", False),
            preflight_done=data.get("preflight_done", False),
            started_at=data.get("started_at"),
        )

    @property
    def terminal(self) -> bool:
        return self.matrix_status not in ("pending", "running")


def fresh_state(bindings: Mapping[str, LaneBinding]) -> MatrixState:
    return MatrixState(lanes={lane: LaneState(binding=binding) for lane, binding in sorted(bindings.items())})


def save_state(path: Path, state: MatrixState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_state(path: Path) -> MatrixState | None:
    if not path.is_file():
        return None
    return MatrixState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")


def interleaved_launch_order(lanes: Mapping[str, LaneState]) -> list[str]:
    """Lane start order, round-robin across arms (user decision, 2026-09-28).

    Lane names sort by arm (SDO w0-w3, Codex w4-w7), so staggering lanes in
    plain sorted order would give one arm a multi-stagger head start over the
    other. If the matrix is stopped partway (quota hard stop), that would
    leave lopsided partial data: one arm well progressed, the other barely
    started. Interleaving keeps every arm's lanes starting close together in
    time, so a partial run's data stays roughly balanced across arms.
    """

    by_arm: dict[str, list[str]] = {}
    for lane in sorted(lanes):
        by_arm.setdefault(lanes[lane].binding.arm, []).append(lane)
    order: list[str] = []
    for round_lanes in itertools.zip_longest(*by_arm.values()):
        order.extend(lane for lane in round_lanes if lane is not None)
    return order


# --------------------------------------------------------------------------- orchestration


def run_matrix(
    *,
    phase1_dir: Path,
    launch_dir: Path,
    cluster_provider: ClusterProvider,
    preflight_runner: PreflightRunner,
    quota_reader: QuotaReader,
    host_monitor: HostMonitor,
    process_runner: ProcessRunner,
    gate: QuotaGate | None = None,
    planned_budget: TokenBudgetEstimate | None = None,
    stagger_seconds: float = 120.0,
    sample_interval_seconds: float = 30.0,
    tick_seconds: float = 1.0,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_ticks: int | None = None,
) -> MatrixState:
    """Run (or resume) the phase-1 matrix; returns the final :class:`MatrixState`.

    Every side effect goes through the injected dependencies, so this
    function never touches a real cluster, spends real quota, or starts a
    real process unless the caller's implementations do.

    ``planned_budget`` is the token-calibrated cost of the matrix (plus any
    smoke run) the caller is about to launch (see ``phase1_budget.py``);
    it defaults to the full phase-1 matrix. The start gate checks
    ``used_percent + planned_budget.nominal_percent`` (EXPECTED cost; 2026-10
    gate change, ``HARNESS_DECISIONS.md``) against ``gate.stop_percent``, and
    the decision (with every number that went into it, including the worst
    case, kept for information) is logged to
    ``<launch_dir>/gate_decision.json`` before anything is launched.
    """

    gate = gate or QuotaGate()
    planned_budget = planned_budget or full_matrix_budget()
    state_path = launch_dir / "state.json"
    host_log = launch_dir / "host_samples.jsonl"
    quota_log = launch_dir / "quota_log.jsonl"
    gate_decision_path = launch_dir / "gate_decision.json"

    bindings = bind_lanes(phase1_dir)
    state = load_state(state_path)
    if state is None:
        state = fresh_state(bindings)
        save_state(state_path, state)
    if state.terminal:
        return state

    if not state.cluster_verified:
        for lane in sorted(bindings):
            cluster_provider.ensure_lane(lane)
        state.cluster_verified = True
        save_state(state_path, state)

    if not state.preflight_done:
        used_at_start = quota_reader.used_percent()
        started = gate.can_start_matrix(used_at_start, planned_budget.nominal_percent)
        gate_decision = {
            "used_percent_at_start": used_at_start,
            "planned_label": planned_budget.label,
            "planned_nominal_tokens": planned_budget.nominal_tokens,
            "planned_worst_case_tokens": planned_budget.worst_case_tokens,
            "planned_nominal_percent": planned_budget.nominal_percent,
            "planned_worst_case_percent": planned_budget.worst_case_percent,
            "gated_on": "nominal_percent",  # 2026-10 gate change: expected cost, not the 2x worst case
            "stop_percent": gate.stop_percent,
            "decision": "start" if started else "abort_quota_start",
        }
        gate_decision_path.parent.mkdir(parents=True, exist_ok=True)
        gate_decision_path.write_text(json.dumps(gate_decision, indent=2), encoding="utf-8")
        append_jsonl(quota_log, {"lane": None, "event": "gate_decision", **gate_decision})
        if not started:
            state.matrix_status = "aborted_quota_start"
            save_state(state_path, state)
            return state
        for _lane, lane_state in sorted(state.lanes.items()):
            report = preflight_runner(lane_state.binding)
            if not report.ok:
                state.matrix_status = "aborted_preflight"
                save_state(state_path, state)
                return state
        state.preflight_done = True
        state.matrix_status = "running"
        state.started_at = clock()
        save_state(state_path, state)

    handles: dict[str, ProcessHandle] = {}
    lane_started_at: dict[str, float] = {}
    lane_used_before: dict[str, float | None] = {}
    scheduled_start = {
        lane: (state.started_at or clock()) + index * stagger_seconds
        for index, lane in enumerate(interleaved_launch_order(state.lanes))
    }

    # Resume: a lane this process left "running" was mid-flight when the
    # launcher was interrupted (no live handle survives a restart). Resume it
    # immediately, from its recorded run directory when SREGym's own "New
    # pipeline/experiment: <dir>" announcement was captured before the
    # interruption; otherwise fall back to relaunching its config.
    for lane, lane_state in sorted(state.lanes.items()):
        if lane_state.status == "running":
            resume_dir = Path(lane_state.run_dir) if lane_state.run_dir else None
            env = dict(os.environ)
            env["SREGYM_KIND_CLUSTER_PREFIX"] = LANE_PREFIX
            env["SREGYM_WORKER_ID_OFFSET"] = str(lane_offset(lane))
            now = clock()
            handles[lane] = process_runner.start(lane_state.binding, resume_dir=resume_dir, env=env)
            lane_started_at[lane] = now
            lane_used_before[lane] = quota_reader.used_percent()

    last_sample = 0.0
    ticks = 0
    while True:
        now = clock()
        if now - last_sample >= sample_interval_seconds:
            sample = host_monitor.sample()
            append_jsonl(host_log, asdict(sample))
            last_sample = now

        used_now = quota_reader.used_percent()
        if gate.must_stop_matrix(used_now):
            for lane, lane_state in state.lanes.items():
                if lane_state.status == "running" and lane in handles:
                    handles[lane].terminate()
                    lane_state.status = "aborted_matrix_stop"
            state.matrix_status = "stopped_quota"
            save_state(state_path, state)
            return state

        for lane, lane_state in sorted(state.lanes.items()):
            if lane_state.status == "pending" and now >= scheduled_start[lane]:
                resume_dir = Path(lane_state.run_dir) if lane_state.run_dir else None
                env = dict(os.environ)
                env["SREGYM_KIND_CLUSTER_PREFIX"] = LANE_PREFIX
                env["SREGYM_WORKER_ID_OFFSET"] = str(lane_offset(lane))
                handles[lane] = process_runner.start(lane_state.binding, resume_dir=resume_dir, env=env)
                lane_state.status = "running"
                lane_started_at[lane] = now
                lane_used_before[lane] = used_now
                save_state(state_path, state)

        for lane, lane_state in sorted(state.lanes.items()):
            if lane_state.status != "running" or lane not in handles:
                continue
            discovered = handles[lane].run_dir()
            if discovered is not None and lane_state.run_dir != str(discovered):
                lane_state.run_dir = str(discovered)
                save_state(state_path, state)
            returncode = handles[lane].poll()
            if returncode is not None:
                used_after = quota_reader.used_percent()
                before = lane_used_before.get(lane)
                delta = None
                if before is not None and used_after is not None:
                    delta = max(0.0, used_after - before)
                    lane_state.cumulative_percent += delta
                record = LaneRunRecord(
                    attempt=len(lane_state.records) + 1,
                    started_at=lane_started_at.get(lane, now),
                    finished_at=now,
                    used_percent_before=before,
                    used_percent_after=used_after,
                    attributed_delta_percent=delta,
                    returncode=returncode,
                )
                lane_state.records.append(record)
                append_jsonl(quota_log, {"lane": lane, **asdict(record)})
                lane_state.status = "done" if returncode == 0 else "failed"
                del handles[lane]
                save_state(state_path, state)
            else:
                before = lane_used_before.get(lane)
                live_delta = max(0.0, used_now - before) if before is not None and used_now is not None else 0.0
                live_estimate = lane_state.cumulative_percent + live_delta
                if gate.lane_over_budget(live_estimate, lane_state.binding.budget_percent):
                    handles[lane].terminate()
                    lane_state.status = "aborted_budget"
                    del handles[lane]
                    save_state(state_path, state)

        if all(lane_state.status in ("done", "failed", "aborted_budget") for lane_state in state.lanes.values()):
            state.matrix_status = "completed"
            save_state(state_path, state)
            return state

        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            save_state(state_path, state)
            return state
        sleep(tick_seconds)


# --------------------------------------------------------------------------- CLI


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the SREGym assurance phase-1 live matrix.")
    parser.add_argument("--phase1-dir", type=Path, default=None, help="defaults to this repo's phase1 experiments dir")
    parser.add_argument("--launch-dir", type=Path, default=None, help="defaults to <phase1-dir>/.launch")
    parser.add_argument("--dry-run", action="store_true", help="stub cluster, quota and process calls; touch nothing")
    parser.add_argument("--stagger-seconds", type=float, default=120.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=30.0)
    parser.add_argument("--tick-seconds", type=float, default=5.0)
    parser.add_argument(
        "--matrix",
        choices=("full", "reduced"),
        default="full",
        help="the phase-1 matrix preset to plan for (default: full, PLAN.md's own matrix)",
    )
    parser.add_argument(
        "--stop-percent",
        type=float,
        default=96.0,
        help="global quota stop line for this run (PLAN.md (d)'s own hard stop is 97%%; "
        "this launcher's default keeps one point of margin under it)",
    )
    parser.add_argument("--lane-abort-multiplier", type=float, default=1.5)
    return parser


class _DryRunClusterProvider:
    def ensure_lane(self, lane: str) -> ClusterCheck:
        return ClusterCheck(lane, "dry-run: not touched")


class _DryRunQuotaReader:
    def used_percent(self) -> float | None:
        return None


class _DryRunHostMonitor:
    def sample(self) -> HostSample:
        return HostSample(at=time.time(), load1=None, load5=None, load15=None, disk_free_bytes={})


class _DryRunHandle:
    def poll(self) -> int | None:
        return 0

    def terminate(self) -> None:
        return None

    def run_dir(self) -> Path | None:
        return None


class _DryRunProcessRunner:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, binding: LaneBinding, *, resume_dir: Path | None, env: Mapping[str, str]) -> ProcessHandle:
        self.started.append(binding.lane)
        return _DryRunHandle()


def _dry_run_preflight_runner(binding: LaneBinding) -> PreflightReport:
    from benchmarks.sregym.runner.preflight import PreflightReport

    return PreflightReport(checks=(), facts={"dry_run": True})


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    project_root = Path(__file__).resolve().parents[3]
    phase1_dir = args.phase1_dir or project_root / "benchmarks" / "sregym" / "experiments" / "assurance" / "phase1"
    launch_dir = args.launch_dir or phase1_dir / ".launch"
    sregym_dir = Path(os.environ.get("SDO_SREGYM_DIR", project_root / "third_party" / "sregym")).resolve()

    gate = QuotaGate(stop_percent=args.stop_percent, lane_abort_multiplier=args.lane_abort_multiplier)

    dry_run = args.dry_run
    if dry_run:
        cluster_provider: ClusterProvider = _DryRunClusterProvider()
        quota_reader: QuotaReader = _DryRunQuotaReader()
        host_monitor: HostMonitor = _DryRunHostMonitor()
        process_runner: ProcessRunner = _DryRunProcessRunner()
    else:
        cluster_provider = SystemClusterProvider()
        quota_reader = SystemQuotaReader()
        host_monitor = SystemHostMonitor({"logs": sregym_dir / "logs", "project": project_root})
        process_runner = SubprocessProcessRunner(project_root, launch_dir / "logs")

    # 2026-10 pivot: plan the smoke run and the selected matrix in tokens,
    # calibrated from measured runs (phase1_budget.py), auto-shrinking the
    # matrix (never the smoke run, which is fixed and small) until the
    # combined EXPECTED (nominal) cost clears the stop line (2026-10 gate
    # change: gating on the 2x worst case was too conservative and shrank the
    # full matrix down for no measured reason; the worst case is still
    # computed and printed here for information). Print the plan either way.
    current_used_percent = quota_reader.used_percent()
    smoke = smoke_budget()
    start_plan = FULL_MATRIX if args.matrix == "full" else REDUCED_MATRIX
    effective_stop_for_matrix = args.stop_percent - smoke.nominal_percent
    shrink_result = autoshrink_to_fit(
        current_used_percent=current_used_percent or 0.0,
        stop_percent=effective_stop_for_matrix,
        start=start_plan,
    )
    combined_breakdown = {f"smoke/{name}": value for name, value in smoke.breakdown.items()}
    combined_breakdown.update(
        {f"{args.matrix}/{name}": value for name, value in shrink_result.estimate.breakdown.items()}
    )
    planned_budget = TokenBudgetEstimate(label=f"smoke+{shrink_result.estimate.label}", breakdown=combined_breakdown)

    print(f"phase-1 matrix budget (--matrix {args.matrix}, current used {current_used_percent}):")
    print(f"smoke: {smoke.render()}")
    print(shrink_result.explain())
    print(
        f"combined expected (nominal) cost: {planned_budget.nominal_percent:.3f} pt (gates the start decision) "
        f"[worst case {planned_budget.worst_case_percent:.3f} pt, informational only] "
        f"(stop line {args.stop_percent:.1f}%, current {current_used_percent})"
    )
    if not shrink_result.fits:
        print("WARNING: even the floor plan does not fit this quota headroom; the launcher will abort at the gate.")

    if dry_run:
        preflight_runner: PreflightRunner = _dry_run_preflight_runner
    else:
        effective_quota_threshold = max(0.0, min(100.0, args.stop_percent - planned_budget.nominal_percent))
        preflight_runner = default_preflight_runner(
            project_root=project_root, sregym_dir=sregym_dir, max_quota_used_percent=effective_quota_threshold
        )

    state = run_matrix(
        phase1_dir=phase1_dir,
        launch_dir=launch_dir,
        cluster_provider=cluster_provider,
        preflight_runner=preflight_runner,
        quota_reader=quota_reader,
        host_monitor=host_monitor,
        process_runner=process_runner,
        gate=gate,
        planned_budget=planned_budget,
        stagger_seconds=args.stagger_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
        tick_seconds=args.tick_seconds,
    )
    print(json.dumps(state.to_dict(), indent=2))
    return 0 if state.matrix_status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
