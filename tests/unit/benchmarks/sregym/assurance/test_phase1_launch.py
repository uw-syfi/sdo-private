from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.sregym.assurance.phase1_launch import (
    ClusterCheck,
    HostSample,
    QuotaGate,
    bind_lanes,
    lane_offset,
    load_state,
    parse_lane_from_header,
    run_matrix,
)
from benchmarks.sregym.runner.preflight import PreflightCheck, PreflightReport

PHASE1_HEADERS = {
    "sdo_codex_luna_assure_p1_a.toml": "assure-w0",
    "sdo_codex_luna_assure_p1_b.toml": "assure-w1",
    "sdo_codex_luna_assure_p1_c.toml": "assure-w2",
    "sdo_codex_luna_assure_p1_d.toml": "assure-w3",
    "codex_luna_verify_assure_p1_1.toml": "assure-w4",
    "codex_luna_verify_assure_p1_2.toml": "assure-w5",
    "codex_luna_verify_assure_p1_3.toml": "assure-w6",
    "codex_luna_verify_assure_p1_4.toml": "assure-w7",
}


def _write_phase1_dir(tmp_path: Path) -> Path:
    phase1 = tmp_path / "phase1"
    phase1.mkdir()
    for name, lane in PHASE1_HEADERS.items():
        (phase1 / name).write_text(
            f'# Assurance phase 1 config; lane {lane}.\n\n[runner]\nagent = "codex"\n', encoding="utf-8"
        )
    return phase1


# --------------------------------------------------------------------------- lane binding


def test_parse_lane_from_header_reads_the_declared_lane(tmp_path: Path) -> None:
    path = tmp_path / "x.toml"
    path.write_text("# header; lane assure-w3.\n\n[runner]\n", encoding="utf-8")
    assert parse_lane_from_header(path) == "assure-w3"


def test_parse_lane_from_header_rejects_a_missing_or_duplicate_lane(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    missing.write_text("# no lane here\n\n[runner]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one lane"):
        parse_lane_from_header(missing)

    duplicate = tmp_path / "dup.toml"
    duplicate.write_text("# lane assure-w0 and also lane assure-w1\n\n[runner]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one lane"):
        parse_lane_from_header(duplicate)


def test_bind_lanes_binds_every_config_to_its_declared_lane_and_arm(tmp_path: Path) -> None:
    phase1 = _write_phase1_dir(tmp_path)
    bindings = bind_lanes(phase1)
    assert set(bindings) == {f"assure-w{n}" for n in range(8)}
    assert bindings["assure-w0"].arm == "sdo_codex"
    assert bindings["assure-w4"].arm == "codex_verify"
    assert bindings["assure-w7"].arm == "codex_verify"
    # Each arm's per-lane budget is its PLAN.md (d) row total, divided across its lanes.
    assert bindings["assure-w0"].budget_percent == pytest.approx(6.2 / 4)
    assert bindings["assure-w4"].budget_percent == pytest.approx(2.7 / 4)


def test_bind_lanes_rejects_a_missing_lane(tmp_path: Path) -> None:
    phase1 = _write_phase1_dir(tmp_path)
    (phase1 / "sdo_codex_luna_assure_p1_a.toml").unlink()
    with pytest.raises(ValueError, match="expected lanes"):
        bind_lanes(phase1)


def test_bind_lanes_rejects_two_configs_claiming_the_same_lane(tmp_path: Path) -> None:
    phase1 = _write_phase1_dir(tmp_path)
    (phase1 / "sdo_codex_luna_assure_p1_extra.toml").write_text(
        '# Assurance phase 1 config; lane assure-w0.\n\n[runner]\nagent = "sdo_codex"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="bound twice"):
        bind_lanes(phase1)


def test_the_real_phase1_directory_binds_cleanly() -> None:
    real = Path(__file__).resolve().parents[5] / "benchmarks" / "sregym" / "experiments" / "assurance" / "phase1"
    assert len(bind_lanes(real)) == 8


def test_lane_offset_parses_the_trailing_worker_number() -> None:
    assert lane_offset("assure-w0") == 0
    assert lane_offset("assure-w7") == 7
    with pytest.raises(ValueError, match="not a phase-1 lane name"):
        lane_offset("sregym-w0")


# --------------------------------------------------------------------------- quota gate


class TestQuotaGate:
    def test_starts_only_if_current_plus_planned_worst_case_clears_the_stop_line(self) -> None:
        gate = QuotaGate(stop_percent=96.0)
        assert gate.can_start_matrix(90.0, 6.0)  # 90 + 6 == 96
        assert not gate.can_start_matrix(90.1, 6.0)  # 90.1 + 6 > 96
        assert gate.can_start_matrix(None, 6.0)  # unknown quota does not block a launch

    def test_can_start_matrix_rejects_a_negative_planned_worst_case(self) -> None:
        gate = QuotaGate()
        with pytest.raises(ValueError, match="planned_worst_case_percent"):
            gate.can_start_matrix(50.0, -1.0)

    def test_stops_at_or_above_the_threshold(self) -> None:
        gate = QuotaGate(stop_percent=97.0)
        assert gate.must_stop_matrix(97.0)
        assert not gate.must_stop_matrix(96.9)
        assert not gate.must_stop_matrix(None)

    def test_aborts_a_lane_only_past_one_and_a_half_times_its_budget(self) -> None:
        gate = QuotaGate(lane_abort_multiplier=1.5)
        assert not gate.lane_over_budget(1.5, budget_percent=1.0)
        assert gate.lane_over_budget(1.51, budget_percent=1.0)

    def test_rejects_an_inconsistent_or_degenerate_configuration(self) -> None:
        with pytest.raises(ValueError, match="stop_percent"):
            QuotaGate(stop_percent=150.0)
        with pytest.raises(ValueError, match="lane_abort_multiplier"):
            QuotaGate(lane_abort_multiplier=1.0)


# --------------------------------------------------------------------------- run_matrix fakes


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeClusterProvider:
    def __init__(self) -> None:
        self.checked: list[str] = []

    def ensure_lane(self, lane: str) -> ClusterCheck:
        self.checked.append(lane)
        return ClusterCheck(lane, "ok")


class FakeQuotaReader:
    def __init__(self, clock: FakeClock, *, start: float = 10.0, rate: float = 0.0) -> None:
        self._clock = clock
        self._start = start
        self._rate = rate

    def used_percent(self) -> float | None:
        return self._start + self._rate * self._clock.now


class FakeHostMonitor:
    def sample(self) -> HostSample:
        return HostSample(at=0.0, load1=1.0, load5=1.0, load15=1.0, disk_free_bytes={"logs": 10**12})


class FakeHandle:
    def __init__(self, ticks_to_finish: int, returncode: int = 0, discovered_dir: Path | None = None) -> None:
        self._ticks = ticks_to_finish
        self._returncode = returncode
        self._discovered_dir = discovered_dir
        self.terminated = False

    def poll(self) -> int | None:
        if self._ticks > 0:
            self._ticks -= 1
            return None
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._ticks = 0

    def run_dir(self) -> Path | None:
        return self._discovered_dir


class FakeProcessRunner:
    def __init__(
        self,
        ticks_to_finish: dict[str, int] | int = 1,
        *,
        returncodes: dict[str, int] | None = None,
        discovered_dir: Path | None = None,
    ) -> None:
        self._ticks_to_finish = ticks_to_finish
        self._returncodes = returncodes or {}
        self._discovered_dir = discovered_dir
        self.started: list[tuple[str, Path | None]] = []
        self.handles: dict[str, FakeHandle] = {}

    def start(self, binding, *, resume_dir, env) -> FakeHandle:
        self.started.append((binding.lane, resume_dir))
        ticks = (
            self._ticks_to_finish
            if isinstance(self._ticks_to_finish, int)
            else self._ticks_to_finish.get(binding.lane, 1)
        )
        handle = FakeHandle(ticks, self._returncodes.get(binding.lane, 0), self._discovered_dir)
        self.handles[binding.lane] = handle
        return handle


def _ok_preflight(binding) -> PreflightReport:
    return PreflightReport(checks=(), facts={})


def _failing_preflight_for(bad_lane: str):
    def run(binding) -> PreflightReport:
        if binding.lane == bad_lane:
            return PreflightReport(checks=(PreflightCheck("disk", "fail", "no space"),), facts={})
        return PreflightReport(checks=(), facts={})

    return run


def _run(tmp_path: Path, **overrides):
    phase1 = overrides.pop("phase1_dir", None) or _write_phase1_dir(tmp_path)
    clock = overrides.pop("clock_obj", None) or FakeClock()
    kwargs = {
        "phase1_dir": phase1,
        "launch_dir": tmp_path / "launch",
        "cluster_provider": FakeClusterProvider(),
        "preflight_runner": _ok_preflight,
        "quota_reader": FakeQuotaReader(clock),
        "host_monitor": FakeHostMonitor(),
        "process_runner": FakeProcessRunner(ticks_to_finish=1),
        "stagger_seconds": 0.0,
        "sample_interval_seconds": 1000.0,
        "tick_seconds": 1.0,
        "clock": clock.time,
        "sleep": clock.sleep,
        "max_ticks": 20,
    }
    kwargs.update(overrides)
    return run_matrix(**kwargs), phase1, clock


# --------------------------------------------------------------------------- run_matrix


def test_run_matrix_completes_every_lane(tmp_path: Path) -> None:
    state, _, _ = _run(tmp_path)
    assert state.matrix_status == "completed"
    assert all(lane_state.status == "done" for lane_state in state.lanes.values())


def test_run_matrix_aborts_the_whole_matrix_on_any_failing_lane_preflight(tmp_path: Path) -> None:
    process_runner = FakeProcessRunner()
    state, _, _ = _run(
        tmp_path,
        preflight_runner=_failing_preflight_for("assure-w6"),
        process_runner=process_runner,
    )
    assert state.matrix_status == "aborted_preflight"
    assert process_runner.started == []  # nothing launched once one lane fails


def test_run_matrix_does_not_start_when_current_plus_planned_worst_case_exceeds_the_stop_line(tmp_path: Path) -> None:
    # Default gate (stop_percent=97.0) + default planned budget (the full
    # matrix, worst case ~15.4 pt): 90% used leaves no room (90 + 15.4 > 97).
    clock = FakeClock()
    process_runner = FakeProcessRunner()
    state, _, _ = _run(
        tmp_path,
        clock_obj=clock,
        quota_reader=FakeQuotaReader(clock, start=90.0),
        process_runner=process_runner,
    )
    assert state.matrix_status == "aborted_quota_start"
    assert process_runner.started == []


def test_run_matrix_starts_when_current_plus_planned_worst_case_clears_the_stop_line(tmp_path: Path) -> None:
    clock = FakeClock()
    state, _, _ = _run(tmp_path, clock_obj=clock, quota_reader=FakeQuotaReader(clock, start=10.0))
    assert state.matrix_status == "completed"


def test_run_matrix_logs_the_gate_decision(tmp_path: Path) -> None:
    clock = FakeClock()
    state, phase1, _ = _run(tmp_path, clock_obj=clock, quota_reader=FakeQuotaReader(clock, start=90.0))
    assert state.matrix_status == "aborted_quota_start"
    decision = json.loads((tmp_path / "launch" / "gate_decision.json").read_text(encoding="utf-8"))
    assert decision["used_percent_at_start"] == 90.0
    assert decision["decision"] == "abort_quota_start"
    assert decision["stop_percent"] == 97.0
    assert decision["planned_worst_case_percent"] > 0


def test_run_matrix_stops_hard_at_the_quota_stop_threshold_and_terminates_running_lanes(tmp_path: Path) -> None:
    clock = FakeClock()
    process_runner = FakeProcessRunner(ticks_to_finish=100_000)  # never finishes on its own
    state, _, _ = _run(
        tmp_path,
        clock_obj=clock,
        # 40% at launch (passes the <=50% start gate); one tick later it is 100%,
        # crossing the 97% stop threshold before any lane's own budget would.
        quota_reader=FakeQuotaReader(clock, start=40.0, rate=60.0),
        process_runner=process_runner,
        max_ticks=50,
    )
    assert state.matrix_status == "stopped_quota"
    assert all(lane_state.status == "aborted_matrix_stop" for lane_state in state.lanes.values())
    assert all(handle.terminated for handle in process_runner.handles.values())


def test_run_matrix_staggers_lane_starts(tmp_path: Path) -> None:
    clock = FakeClock()
    process_runner = FakeProcessRunner(ticks_to_finish=100_000)
    state, _, _ = _run(
        tmp_path,
        clock_obj=clock,
        stagger_seconds=100.0,
        tick_seconds=50.0,
        process_runner=process_runner,
        max_ticks=1,
    )
    assert state.matrix_status == "running"
    # Only the first lane (scheduled at t=0) has started after one tick.
    assert len(process_runner.started) == 1
    assert process_runner.started[0][0] == "assure-w0"


def test_run_matrix_aborts_only_the_lane_that_exceeds_its_own_budget(tmp_path: Path) -> None:
    clock = FakeClock()
    process_runner = FakeProcessRunner(ticks_to_finish=100_000)
    state, _, _ = _run(
        tmp_path,
        clock_obj=clock,
        quota_reader=FakeQuotaReader(clock, start=10.0, rate=0.1),
        process_runner=process_runner,
        max_ticks=17,
    )
    # The (sole) Codex arm's per-lane budget is 2.7/4=0.675, abort at 1.0125;
    # by tick 17 (now=16) the shared quota has grown by 1.6, past that but not
    # past the SDO threshold (1.55*1.5=2.325).
    assert state.lanes["assure-w4"].status == "aborted_budget"
    assert state.lanes["assure-w5"].status == "aborted_budget"
    assert state.lanes["assure-w6"].status == "aborted_budget"
    assert state.lanes["assure-w7"].status == "aborted_budget"
    assert state.lanes["assure-w0"].status == "running"
    assert process_runner.handles["assure-w4"].terminated
    assert not process_runner.handles["assure-w0"].terminated


def test_run_matrix_is_resumable_from_a_killed_launcher(tmp_path: Path) -> None:
    phase1 = _write_phase1_dir(tmp_path)
    launch_dir = tmp_path / "launch"
    clock = FakeClock()
    discovered = tmp_path / "logs" / "20261003_pipeline_assure-p1-sdo-a"

    first_runner = FakeProcessRunner(ticks_to_finish=100_000, discovered_dir=discovered)
    first_state = run_matrix(
        phase1_dir=phase1,
        launch_dir=launch_dir,
        cluster_provider=FakeClusterProvider(),
        preflight_runner=_ok_preflight,
        quota_reader=FakeQuotaReader(clock),
        host_monitor=FakeHostMonitor(),
        process_runner=first_runner,
        stagger_seconds=0.0,
        sample_interval_seconds=1000.0,
        tick_seconds=1.0,
        clock=clock.time,
        sleep=clock.sleep,
        max_ticks=2,
    )
    assert first_state.matrix_status == "running"
    assert all(lane_state.status == "running" for lane_state in first_state.lanes.values())
    assert first_state.lanes["assure-w0"].run_dir == str(discovered)

    # Simulate the launcher process dying: state.json survives, but there is no
    # live handle. A fresh call with a brand-new process runner must resume
    # every still-running lane from its recorded run_dir, not its config.
    second_runner = FakeProcessRunner(ticks_to_finish=1)
    second_state = run_matrix(
        phase1_dir=phase1,
        launch_dir=launch_dir,
        cluster_provider=FakeClusterProvider(),
        preflight_runner=_ok_preflight,
        quota_reader=FakeQuotaReader(clock),
        host_monitor=FakeHostMonitor(),
        process_runner=second_runner,
        stagger_seconds=0.0,
        sample_interval_seconds=1000.0,
        tick_seconds=1.0,
        clock=clock.time,
        sleep=clock.sleep,
        max_ticks=20,
    )
    assert ("assure-w0", discovered) in second_runner.started
    assert second_state.matrix_status == "completed"

    persisted = load_state(launch_dir / "state.json")
    assert persisted is not None
    assert persisted.matrix_status == "completed"


def test_run_matrix_is_a_no_op_once_the_matrix_has_reached_a_terminal_state(tmp_path: Path) -> None:
    phase1 = _write_phase1_dir(tmp_path)
    launch_dir = tmp_path / "launch"
    clock = FakeClock()
    process_runner = FakeProcessRunner()
    run_matrix(
        phase1_dir=phase1,
        launch_dir=launch_dir,
        cluster_provider=FakeClusterProvider(),
        preflight_runner=_ok_preflight,
        quota_reader=FakeQuotaReader(clock),
        host_monitor=FakeHostMonitor(),
        process_runner=process_runner,
        stagger_seconds=0.0,
        sample_interval_seconds=1000.0,
        tick_seconds=1.0,
        clock=clock.time,
        sleep=clock.sleep,
        max_ticks=20,
    )
    started_after_first_run = len(process_runner.started)

    second_state = run_matrix(
        phase1_dir=phase1,
        launch_dir=launch_dir,
        cluster_provider=FakeClusterProvider(),
        preflight_runner=_ok_preflight,
        quota_reader=FakeQuotaReader(clock),
        host_monitor=FakeHostMonitor(),
        process_runner=process_runner,
        stagger_seconds=0.0,
        sample_interval_seconds=1000.0,
        tick_seconds=1.0,
        clock=clock.time,
        sleep=clock.sleep,
        max_ticks=20,
    )
    assert second_state.matrix_status == "completed"
    assert len(process_runner.started) == started_after_first_run  # nothing new launched


def test_run_matrix_samples_host_load_and_disk_on_the_configured_interval(tmp_path: Path) -> None:
    clock = FakeClock()
    process_runner = FakeProcessRunner(ticks_to_finish=100_000)
    launch_dir = tmp_path / "launch"
    run_matrix(
        phase1_dir=_write_phase1_dir(tmp_path),
        launch_dir=launch_dir,
        cluster_provider=FakeClusterProvider(),
        preflight_runner=_ok_preflight,
        quota_reader=FakeQuotaReader(clock),
        host_monitor=FakeHostMonitor(),
        process_runner=process_runner,
        stagger_seconds=0.0,
        sample_interval_seconds=30.0,
        tick_seconds=10.0,
        clock=clock.time,
        sleep=clock.sleep,
        max_ticks=10,
    )
    samples = (launch_dir / "host_samples.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(samples) >= 3  # ticks at 0,10,20,...90s with a 30s interval: at least ticks 0/30/60/90


def test_run_matrix_logs_quota_for_every_completed_run(tmp_path: Path) -> None:
    clock = FakeClock()
    launch_dir = tmp_path / "launch"
    run_matrix(
        phase1_dir=_write_phase1_dir(tmp_path),
        launch_dir=launch_dir,
        cluster_provider=FakeClusterProvider(),
        preflight_runner=_ok_preflight,
        quota_reader=FakeQuotaReader(clock, start=10.0, rate=0.01),
        host_monitor=FakeHostMonitor(),
        process_runner=FakeProcessRunner(ticks_to_finish=1),
        stagger_seconds=0.0,
        sample_interval_seconds=1000.0,
        tick_seconds=1.0,
        clock=clock.time,
        sleep=clock.sleep,
        max_ticks=20,
    )
    lines = (launch_dir / "quota_log.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    completion_records = [record for record in records if record.get("event") != "gate_decision"]
    assert len(completion_records) == 8  # one completion record per lane
    assert sum(1 for record in records if record.get("event") == "gate_decision") == 1
    assert {record["lane"] for record in completion_records} == {f"assure-w{n}" for n in range(8)}
    assert all(record["used_percent_before"] is not None for record in completion_records)
