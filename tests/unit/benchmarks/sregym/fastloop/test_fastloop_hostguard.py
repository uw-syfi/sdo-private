from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from benchmarks.sregym.fastloop.hostguard import (
    LoadPolicy,
    StallPolicy,
    StallSample,
    classify_stall,
    main,
    sample_progress,
    wait_for_calm_load,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class Host:
    """A host whose 1-minute load follows a script, one reading per poll."""

    loads: list[float]
    now: float = 0.0
    polls: int = 0
    reads: list[float] = field(default_factory=list)

    def read_load(self) -> float:
        value = self.loads[min(self.polls, len(self.loads) - 1)]
        self.polls += 1
        self.reads.append(value)
        return value

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def _policy(**overrides: float) -> LoadPolicy:
    values = {"max_load": 20.0, "calm_seconds": 60.0, "max_wait_seconds": 600.0, "poll_seconds": 15.0}
    values.update(overrides)
    return LoadPolicy(**values)


def test_a_calm_host_does_not_wait_beyond_the_calm_window() -> None:
    host = Host(loads=[5.0])

    result = wait_for_calm_load(_policy(), host.read_load, host.sleep, host.monotonic)

    assert result.calm is True
    assert result.waited_seconds == 60.0


def test_a_zero_calm_window_returns_at_once_on_a_calm_host() -> None:
    host = Host(loads=[5.0])

    result = wait_for_calm_load(_policy(calm_seconds=0.0), host.read_load, host.sleep, host.monotonic)

    assert result.calm is True
    assert result.waited_seconds == 0.0


def test_a_burst_restarts_the_calm_window() -> None:
    host = Host(loads=[5.0, 5.0, 40.0, 5.0, 5.0, 5.0, 5.0, 5.0])

    result = wait_for_calm_load(_policy(), host.read_load, host.sleep, host.monotonic)

    assert result.calm is True
    # two calm polls, then a burst resets the window, then 60 s of calm again
    assert result.waited_seconds == 15.0 * 2 + 15.0 + 60.0
    assert result.peak_load == 40.0


def test_a_host_that_never_calms_down_is_reported_after_the_maximum_wait() -> None:
    host = Host(loads=[45.0])

    result = wait_for_calm_load(_policy(max_wait_seconds=120.0), host.read_load, host.sleep, host.monotonic)

    assert result.calm is False
    assert result.waited_seconds >= 120.0
    assert result.final_load == 45.0


def test_policies_reject_nonsense() -> None:
    with pytest.raises(ValueError, match="max_load"):
        LoadPolicy(max_load=0.0)
    with pytest.raises(ValueError, match="poll_seconds"):
        LoadPolicy(poll_seconds=0.0)
    with pytest.raises(ValueError, match="stall_seconds"):
        StallPolicy(stall_seconds=0.0)


def _sample(**overrides: object) -> StallSample:
    values: dict[str, object] = {
        "newest_file_age_seconds": 30.0,
        "controller_installed": False,
        "seconds_since_controller_install": None,
        "load": 8.0,
    }
    values.update(overrides)
    return StallSample(**values)  # type: ignore[arg-type]


def test_recent_writes_mean_progress() -> None:
    verdict = classify_stall(_sample(newest_file_age_seconds=120.0), StallPolicy(stall_seconds=600.0))

    assert verdict.kind == "progressing"


def test_a_stall_under_heavy_host_load_is_infra_and_says_to_retry_once_when_calm() -> None:
    verdict = classify_stall(_sample(newest_file_age_seconds=900.0, load=38.0), StallPolicy())

    assert verdict.kind == "infra"
    assert "load" in verdict.reason
    assert "retry once" in verdict.action


def test_a_quiet_controller_with_no_incident_is_a_detection_gap_not_a_hang() -> None:
    sample = _sample(
        newest_file_age_seconds=700.0, controller_installed=True, seconds_since_controller_install=700.0, load=8.0
    )

    verdict = classify_stall(sample, StallPolicy(stall_seconds=600.0))

    assert verdict.kind == "product"
    assert "detection" in verdict.reason
    assert "--detection-timeout" in verdict.action


def test_a_stall_before_the_controller_is_installed_is_a_setup_stall() -> None:
    verdict = classify_stall(_sample(newest_file_age_seconds=900.0, load=8.0), StallPolicy(stall_seconds=600.0))

    assert verdict.kind == "unknown"
    assert "lifecycle" in verdict.reason or "setup" in verdict.reason


def test_sampling_reads_the_newest_file_and_the_controller_state(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "results").mkdir(parents=True)
    old = run / "up.log"
    old.write_text("x", encoding="utf-8")
    recent = run / "results" / "incidents.jsonl"
    recent.write_text("{}", encoding="utf-8")
    state = run / "sdo_persistent_controller.json"
    state.write_text("{}", encoding="utf-8")
    now = 10_000.0
    os.utime(old, (now - 5000, now - 5000))
    os.utime(state, (now - 800, now - 800))
    os.utime(recent, (now - 200, now - 200))

    sample = sample_progress([run], now=now, load=3.0)

    assert sample.newest_file_age_seconds == 200.0
    assert sample.controller_installed is True
    assert sample.seconds_since_controller_install == 800.0
    assert sample.load == 3.0


def test_sampling_a_missing_directory_has_no_progress_evidence(tmp_path: Path) -> None:
    sample = sample_progress([tmp_path / "missing"], now=1000.0, load=1.0)

    assert sample.newest_file_age_seconds is None
    assert classify_stall(sample, StallPolicy()).kind == "unknown"


def test_the_watch_command_exits_nonzero_on_a_stall_and_prints_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    log = run / "run.log"
    log.write_text("x", encoding="utf-8")
    os.utime(log, (1.0, 1.0))  # decades old

    code = main(["watch", "--path", str(run), "--stall-seconds", "600"])

    assert code != 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["kind"] in {"infra", "product", "unknown"}
    assert verdict["action"]


def test_the_watch_command_exits_zero_while_files_are_moving(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "run.log").write_text("x", encoding="utf-8")

    assert main(["watch", "--path", str(run)]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "progressing"
