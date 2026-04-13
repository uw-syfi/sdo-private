"""Tests for the run classifier."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app_operator.run_classifier import (
    classify_run,
    classify_workdir,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures for building fake run directories
# ---------------------------------------------------------------------------


def _write_trajectory(run_dir: Path, status: str, deploy_attempts: int) -> None:
    """Write a minimal trajectory JSON."""
    traj_dir = run_dir / ".sds" / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)

    calls = []
    deployment = []
    call_id = 1

    # exploration call
    calls.append({"call_id": call_id, "phase": "exploration"})
    call_id += 1

    # deployment calls
    for i in range(1, deploy_attempts + 1):
        calls.append(
            {
                "call_id": call_id,
                "phase": "deployment",
                "context": {"attempt": i},
            }
        )
        deployment.append({"messages": []})
        call_id += 1

    # monitoring call
    calls.append({"call_id": call_id, "phase": "monitoring"})

    traj = {
        "metadata": {"status": status},
        "calls": calls,
        "deployment": deployment,
    }
    (traj_dir / "trajectory_20260101-000000.json").write_text(json.dumps(traj))


def _write_health_log(
    run_dir: Path,
    attempt: int,
    exit_code: int,
    stdout_body: str = "",
    recheck: bool = False,
) -> None:
    """Write a health check (or recheck) log file."""
    logs_dir = run_dir / ".sds" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    prefix = "health_recheck_attempt" if recheck else "health_check_attempt"
    status = "PASSED" if exit_code == 0 else "FAILED"
    content = (
        f"=== Health Check Output ===\nExit Code: {exit_code}\nStatus: {status}\n\n=== STDOUT ===\n{stdout_body}\n"
    )
    (logs_dir / f"{prefix}_{attempt}.log").write_text(content)


def _write_deploy_log(run_dir: Path, attempt: int) -> None:
    """Write a deploy attempt log file."""
    logs_dir = run_dir / ".sds" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"deploy_attempt_{attempt}.log").write_text(f"deploy attempt {attempt}\n")


def _write_monitor_log(
    run_dir: Path,
    summary: str,
    body: str = "",
) -> None:
    """Write a monitor check log."""
    monitor_dir = run_dir / ".sds" / "logs" / "monitor"
    monitor_dir.mkdir(parents=True, exist_ok=True)
    content = f"=== Agent Analysis ===\n<exec_summary>{summary}</exec_summary>\n\n{body}\n"
    (monitor_dir / "check_1_20260101-000000.log").write_text(content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTrueSuccess:
    def test_single_attempt_clean_pass(self, tmp_path):
        run_dir = tmp_path / "iter1_c1_app"
        _write_trajectory(run_dir, "completed", deploy_attempts=1)
        _write_deploy_log(run_dir, 1)
        _write_health_log(
            run_dir,
            1,
            exit_code=0,
            stdout_body="Total Checks: 10\nPassed: 10\nFailed: 0\nOverall Status: HEALTHY",
        )
        _write_monitor_log(run_dir, "The application is healthy and fully operational.")

        result = classify_run(run_dir)

        assert result.label == "true_success"
        assert result.trajectory_status == "completed"
        assert result.final_health_exit_code == 0
        assert result.health_contradictions == []
        assert result.monitor_concerns == []


class TestRecoveredSuccess:
    def test_multiple_deploy_attempts(self, tmp_path):
        run_dir = tmp_path / "iter2_c1_app"
        _write_trajectory(run_dir, "completed", deploy_attempts=3)
        for i in range(1, 4):
            _write_deploy_log(run_dir, i)
        _write_health_log(run_dir, 3, exit_code=0, stdout_body="HEALTHY")
        _write_monitor_log(run_dir, "The application is healthy.")

        result = classify_run(run_dir)

        assert result.label == "recovered_success"
        assert result.log_deploy_attempts == 3

    def test_health_failed_then_recheck_passed(self, tmp_path):
        run_dir = tmp_path / "iter3_c1_app"
        _write_trajectory(run_dir, "completed", deploy_attempts=1)
        _write_deploy_log(run_dir, 1)
        _write_health_log(run_dir, 1, exit_code=1, stdout_body="FAIL: service down")
        _write_health_log(
            run_dir,
            1,
            exit_code=0,
            stdout_body="Total Checks: 10\nPassed: 10\nOverall Status: HEALTHY",
            recheck=True,
        )
        _write_monitor_log(run_dir, "The application is healthy after fixes.")

        result = classify_run(run_dir)

        assert result.label == "recovered_success"
        assert "Health check failed before passing" in result.reasons[0]


class TestFalsePositive:
    def test_health_exit_0_but_stdout_shows_failures(self, tmp_path):
        """Reproduces the iter4_c2 false positive pattern."""
        run_dir = tmp_path / "iter4_c2_app"
        _write_trajectory(run_dir, "completed", deploy_attempts=1)
        _write_deploy_log(run_dir, 1)
        _write_health_log(
            run_dir,
            1,
            exit_code=0,
            stdout_body=("All services are up and running (1/1 running)\nOverall Status: HEALTHY\nHealth Score: 100%"),
        )
        # Monitor contradicts the health check
        _write_monitor_log(
            run_dir,
            "The application is unhealthy despite the PASSED status because the frontend service is down.",
            body="Overall Health: CRITICAL",
        )

        result = classify_run(run_dir)

        assert result.label == "false_positive"
        assert any("monitor" in r.lower() for r in result.reasons)

    def test_health_exit_0_with_failure_in_stderr(self, tmp_path):
        """Health check exit 0 but stderr says service is not running."""
        run_dir = tmp_path / "iter4_c2_stderr"
        _write_trajectory(run_dir, "completed", deploy_attempts=1)
        _write_deploy_log(run_dir, 1)
        _write_health_log(
            run_dir,
            1,
            exit_code=0,
            stdout_body=('PASSED: All checks\nservice "frontend" is not running\nOverall Status: HEALTHY'),
        )

        result = classify_run(run_dir)

        assert result.label == "false_positive"
        assert any("not running" in c for c in result.health_contradictions)

    def test_health_exit_0_but_not_healthy_in_stdout(self, tmp_path):
        """Reproduces the iter8_c4 false positive: exit 0 but Not Healthy."""
        run_dir = tmp_path / "iter8_c4_app"
        _write_trajectory(run_dir, "completed", deploy_attempts=1)
        _write_deploy_log(run_dir, 1)
        _write_health_log(
            run_dir,
            1,
            exit_code=0,
            stdout_body=("Total Checks: 4\nPassed: 2\nFailed: 2\nOverall Status: Not Healthy"),
        )

        result = classify_run(run_dir)

        assert result.label == "false_positive"
        assert any("Not Healthy" in c or "not running" in c or "Failed" in c for c in result.health_contradictions)


class TestTelemetryInconsistent:
    def test_trajectory_attempts_mismatch(self, tmp_path):
        """Reproduces iter6_c1: trajectory says 1 attempt but 4 deploy logs exist."""
        run_dir = tmp_path / "iter6_c1_app"
        # Trajectory only records 1 deployment call (attempt 4)
        traj_dir = run_dir / ".sds" / "trajectories"
        traj_dir.mkdir(parents=True, exist_ok=True)
        traj = {
            "metadata": {"status": "completed"},
            "calls": [
                {"call_id": 1, "phase": "exploration"},
                {"call_id": 2, "phase": "deployment", "context": {"attempt": 4}},
                {"call_id": 3, "phase": "monitoring"},
            ],
            "deployment": [{"messages": []}],
        }
        (traj_dir / "trajectory_20260101-000000.json").write_text(json.dumps(traj))

        # But 4 deploy logs exist
        for i in range(1, 5):
            _write_deploy_log(run_dir, i)
        _write_health_log(
            run_dir,
            4,
            exit_code=0,
            stdout_body="Overall Status: HEALTHY\nPassed: 10\nFailed: 0",
        )

        result = classify_run(run_dir)

        assert result.label == "telemetry_inconsistent"
        assert "1" in result.reasons[0]
        assert "4" in result.reasons[0]


class TestTrajFailedHealthPassed:
    def test_trajectory_failed_but_health_passed_cleanly(self, tmp_path):
        """Trajectory says failed but health check passed with no contradictions.

        Health check is the ground truth — classify as recovered_success so the
        candidate is not unfairly penalised for a trajectory status mismatch.
        """
        run_dir = tmp_path / "iter_weird"
        _write_trajectory(run_dir, "failed", deploy_attempts=1)
        _write_deploy_log(run_dir, 1)
        _write_health_log(
            run_dir,
            1,
            exit_code=0,
            stdout_body="Overall Status: HEALTHY",
        )

        result = classify_run(run_dir)

        assert result.label == "recovered_success"
        assert "health check passed cleanly" in result.reasons[0].lower()

    def test_trajectory_failed_and_health_has_contradictions(self, tmp_path):
        """Trajectory says failed AND health stdout has failure signals despite exit 0.

        Both the trajectory status and health contradictions agree the deployment
        failed — classify as false_positive (health check is misleading).
        Reproduces the workdir21 pattern where health score was 100% but services
        were not actually running.
        """
        run_dir = tmp_path / "iter_misleading_health"
        _write_trajectory(run_dir, "failed", deploy_attempts=1)
        _write_deploy_log(run_dir, 1)
        _write_health_log(
            run_dir,
            1,
            exit_code=0,
            stdout_body="Health Score: 100%\nservice is not running\nOverall Status: HEALTHY",
        )

        result = classify_run(run_dir)

        assert result.label == "false_positive"
        assert any("not running" in c for c in result.health_contradictions)


class TestTrueFailure:
    def test_failed_status_and_health(self, tmp_path):
        run_dir = tmp_path / "iter1_c1_fail"
        _write_trajectory(run_dir, "failed", deploy_attempts=3)
        for i in range(1, 4):
            _write_deploy_log(run_dir, i)
        _write_health_log(
            run_dir,
            3,
            exit_code=1,
            stdout_body="Overall Status: UNHEALTHY\nFailed: 10",
        )

        result = classify_run(run_dir)

        assert result.label == "true_failure"

    def test_no_health_logs(self, tmp_path):
        run_dir = tmp_path / "iter1_c1_nohc"
        _write_trajectory(run_dir, "failed", deploy_attempts=1)
        _write_deploy_log(run_dir, 1)

        result = classify_run(run_dir)

        assert result.label == "true_failure"


class TestClassifyWorkdir:
    def test_classifies_all_runs(self, tmp_path):
        workdir = tmp_path / "workdir"
        workdir.mkdir()

        # A true success
        run1 = workdir / "iter1_c1_app"
        _write_trajectory(run1, "completed", deploy_attempts=1)
        _write_deploy_log(run1, 1)
        _write_health_log(run1, 1, exit_code=0, stdout_body="HEALTHY\nPassed: 10")
        _write_monitor_log(run1, "Everything is healthy.")

        # A true failure
        run2 = workdir / "iter1_c2_app"
        _write_trajectory(run2, "failed", deploy_attempts=2)
        _write_deploy_log(run2, 1)
        _write_deploy_log(run2, 2)
        _write_health_log(run2, 2, exit_code=1, stdout_body="UNHEALTHY")

        # A non-run file (should be skipped)
        (workdir / "notes.txt").write_text("not a run")

        results = classify_workdir(workdir)

        assert len(results) == 2
        labels = {r.run_dir: r.label for r in results}
        assert labels["iter1_c1_app"] == "true_success"
        assert labels["iter1_c2_app"] == "true_failure"

    def test_empty_workdir(self, tmp_path):
        workdir = tmp_path / "empty"
        workdir.mkdir()
        assert classify_workdir(workdir) == []


class TestEdgeCases:
    def test_no_trajectory(self, tmp_path):
        run_dir = tmp_path / "iter_no_traj"
        sds_dir = run_dir / ".sds"
        sds_dir.mkdir(parents=True)
        _write_deploy_log(run_dir, 1)

        result = classify_run(run_dir)

        # No trajectory → status is None → treated as failure
        assert result.label == "true_failure"
        assert result.trajectory_status is None

    def test_trajectory_unreadable_but_health_passed(self, tmp_path):
        """When trajectory can't be loaded (e.g. disk full) but health check
        passed, classify as recovered_success rather than telemetry_inconsistent."""
        run_dir = tmp_path / "iter_diskfull"
        # Create a corrupt/empty trajectory file (simulates disk-full write)
        traj_dir = run_dir / ".sds" / "trajectories"
        traj_dir.mkdir(parents=True, exist_ok=True)
        (traj_dir / "trajectory_20260309-020522.json").write_text("")  # empty = unreadable

        _write_deploy_log(run_dir, 1)
        _write_deploy_log(run_dir, 2)
        _write_health_log(
            run_dir,
            2,
            exit_code=0,
            stdout_body="Total Checks: 29\nPassed: 29\nFailed: 0\nOverall Status: HEALTHY",
        )

        result = classify_run(run_dir)

        assert result.label == "recovered_success"
        assert result.trajectory_status is None
        assert any("unavailable" in r for r in result.reasons)

    def test_no_sds_dir(self, tmp_path):
        run_dir = tmp_path / "iter_bare"
        run_dir.mkdir()

        result = classify_run(run_dir)

        assert result.label == "true_failure"

    def test_monitor_with_ansi_codes(self, tmp_path):
        """Monitor log with ANSI escape codes should still be parsed."""
        run_dir = tmp_path / "iter_ansi"
        _write_trajectory(run_dir, "completed", deploy_attempts=1)
        _write_deploy_log(run_dir, 1)
        _write_health_log(run_dir, 1, exit_code=0, stdout_body="HEALTHY\nPassed: 5")
        # Write monitor with ANSI codes embedded
        monitor_dir = run_dir / ".sds" / "logs" / "monitor"
        monitor_dir.mkdir(parents=True, exist_ok=True)
        content = "=== Agent Analysis ===\n<exec_summary>\x1b[0;31mThe application is unhealthy\x1b[0m</exec_summary>\n"
        (monitor_dir / "check_1_20260101-000000.log").write_text(content)

        result = classify_run(run_dir)

        assert result.label == "false_positive"
        assert any("unhealthy" in c for c in result.monitor_concerns)
