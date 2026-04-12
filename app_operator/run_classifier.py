"""Classify experiment runs into quality categories.

Inspects health check logs, monitor logs, and trajectory data to determine
whether a run that the system marked as "success" is actually trustworthy.

Classification labels:
- true_success:            Health check passed cleanly, monitor confirms healthy.
- recovered_success:       Health check failed initially but passed after agent fixes.
- false_positive:          Marked successful but evidence contradicts (monitor says
                           unhealthy/critical, or health stdout shows failures despite
                           exit code 0).
- telemetry_inconsistent:  Trajectory attempt count doesn't match actual log files,
                           or other data integrity issues.
- true_failure:            Run failed and evidence agrees.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_HEALTH_LOG_EXIT_CODE_RE = re.compile(r"^Exit Code:\s*(-?\d+)", re.MULTILINE)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

_HEALTH_STDOUT_FAILURE_PATTERNS = (
    "not running",
    "is not running",
    "Not Healthy",
    "UNHEALTHY",
    "failed to connect",
)

# Patterns where context matters — matched via regex to avoid false hits like
# "Failed: 0" (which indicates zero failures).
_HEALTH_FAILURE_REGEXES = (
    # "[ FAIL ]" or "[FAIL]" markers used in health check scripts
    re.compile(r"\[\s*FAIL\s*\]"),
    # "Failed: N" where N > 0
    re.compile(r"Failed:\s*[1-9]\d*"),
    # "[ERROR]" markers
    re.compile(r"\[\s*ERROR\s*\]"),
)

_MONITOR_CRITICAL_PATTERNS = (
    "critical",
    "unhealthy",
    "not healthy",
    "not running",
    "service is down",
    "inaccessible",
    "outage",
)

_EXEC_SUMMARY_RE = re.compile(r"<exec_summary>(.*?)</exec_summary>", re.DOTALL)


@dataclass
class RunClassification:
    """Classification result for a single experiment run."""

    run_dir: str
    label: str = ""
    trajectory_status: str | None = None
    trajectory_attempts: int = 0
    log_deploy_attempts: int = 0
    log_health_checks: int = 0
    log_health_rechecks: int = 0
    final_health_exit_code: int | None = None
    final_health_passed: bool | None = None
    health_contradictions: list[str] = field(default_factory=list)
    monitor_concerns: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def classify_run(run_dir: Path) -> RunClassification:
    """Classify a single experiment run directory.

    Args:
        run_dir: Path to an experiment run directory containing a ``.sds/``
                 subdirectory with trajectories and logs.

    Returns:
        A RunClassification with the determined label and supporting evidence.
    """
    result = RunClassification(run_dir=run_dir.name)

    # --- 1. Parse trajectory ---
    traj = _load_trajectory(run_dir)
    if traj is not None:
        result.trajectory_status = str(traj.get("metadata", {}).get("status", "unknown"))
        result.trajectory_attempts = len(traj.get("deployment", []))
    else:
        result.trajectory_status = None

    # --- 2. Count actual log files ---
    logs_dir = run_dir / ".sds" / "logs"
    deploy_logs = sorted(logs_dir.glob("deploy_attempt_*.log")) if logs_dir.exists() else []
    health_logs = sorted(logs_dir.glob("health_check_attempt_*.log")) if logs_dir.exists() else []
    recheck_logs = sorted(logs_dir.glob("health_recheck_attempt_*.log")) if logs_dir.exists() else []
    monitor_logs = sorted((logs_dir / "monitor").glob("check_*.log")) if (logs_dir / "monitor").exists() else []

    result.log_deploy_attempts = len(deploy_logs)
    result.log_health_checks = len(health_logs)
    result.log_health_rechecks = len(recheck_logs)

    # --- 3. Analyze final health outcome ---
    # The "final" health result is the last recheck if any, otherwise the last
    # health check.  This mirrors how the deployer decides success.
    final_health_log = None
    if recheck_logs:
        final_health_log = recheck_logs[-1]
    elif health_logs:
        final_health_log = health_logs[-1]

    if final_health_log is not None:
        exit_code, passed, contradictions = _parse_health_log(final_health_log)
        result.final_health_exit_code = exit_code
        result.final_health_passed = passed
        result.health_contradictions = contradictions

    # --- 4. Analyze monitor logs ---
    for monitor_log in monitor_logs:
        concerns = _parse_monitor_log(monitor_log)
        result.monitor_concerns.extend(concerns)

    # --- 5. Check for telemetry inconsistency ---
    # Trajectory call list records deployment attempts with context.attempt;
    # compare against actual deploy log file count.
    traj_deploy_call_count = _count_trajectory_deploy_calls(traj) if traj else 0

    # --- 6. Apply classification rules ---
    is_traj_success = _is_success_status(result.trajectory_status)

    if not is_traj_success:
        # Trajectory says failure
        if result.final_health_passed:
            result.label = "telemetry_inconsistent"
            result.reasons.append(
                f"Trajectory status '{result.trajectory_status}' indicates failure but final health check passed"
            )
        else:
            result.label = "true_failure"
            result.reasons.append(f"Trajectory status '{result.trajectory_status}' and health check agree on failure")
        return result

    # Trajectory says success — validate it
    has_contradictions = bool(result.health_contradictions)
    has_monitor_concerns = bool(result.monitor_concerns)

    if has_contradictions or has_monitor_concerns:
        result.label = "false_positive"
        if has_contradictions:
            result.reasons.append(
                "Health check exit code 0 but stdout contains failure signals: "
                + "; ".join(result.health_contradictions)
            )
        if has_monitor_concerns:
            result.reasons.append("Monitor reported concerns: " + "; ".join(result.monitor_concerns))
        return result

    # Check telemetry consistency
    if (
        traj_deploy_call_count > 0
        and result.log_deploy_attempts > 0
        and traj_deploy_call_count != result.log_deploy_attempts
    ):
        result.label = "telemetry_inconsistent"
        result.reasons.append(
            f"Trajectory records {traj_deploy_call_count} deployment call(s) "
            f"but {result.log_deploy_attempts} deploy log file(s) exist"
        )
        return result

    # Check if it was a recovered success (had failures before final pass)
    had_prior_failures = _had_prior_health_failures(health_logs, recheck_logs)
    if had_prior_failures or result.log_deploy_attempts > 1:
        result.label = "recovered_success"
        if had_prior_failures:
            result.reasons.append("Health check failed before passing on a later attempt")
        if result.log_deploy_attempts > 1:
            result.reasons.append(f"Required {result.log_deploy_attempts} deployment attempts")
        return result

    result.label = "true_success"
    result.reasons.append("Health check passed cleanly on first attempt")
    return result


def classify_workdir(workdir: Path) -> list[RunClassification]:
    """Classify all runs in an eval-execute workdir.

    Args:
        workdir: Path to a workdir containing ``iterN_cM_appname`` directories.

    Returns:
        List of classifications sorted by directory name.
    """
    results = []
    for child in sorted(workdir.iterdir()):
        if not child.is_dir():
            continue
        sds_dir = child / ".sds"
        if not sds_dir.exists():
            continue
        results.append(classify_run(child))
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_trajectory(run_dir: Path) -> dict | None:
    """Load the most recent trajectory JSON from a run directory."""
    traj_dir = run_dir / ".sds" / "trajectories"
    if not traj_dir.exists():
        return None
    traj_files = sorted(traj_dir.glob("trajectory_*.json"), reverse=True)
    if not traj_files:
        return None
    try:
        return json.loads(traj_files[0].read_text())
    except Exception:
        return None


def _parse_health_log(log_path: Path) -> tuple[int | None, bool | None, list[str]]:
    """Parse a health check log file.

    Returns:
        (exit_code, passed_per_exit_code, contradictions)
        where contradictions lists failure signals found in stdout despite
        exit code 0.
    """
    try:
        content = log_path.read_text()
    except Exception:
        return None, None, []

    # Extract exit code
    exit_code = None
    m = _HEALTH_LOG_EXIT_CODE_RE.search(content)
    if m:
        exit_code = int(m.group(1))

    passed = exit_code == 0 if exit_code is not None else None

    # Look for contradictions: exit code 0 but stdout has failure signals
    contradictions: list[str] = []
    if passed:
        clean = _ANSI_ESCAPE.sub("", content)
        contradictions.extend(p for p in _HEALTH_STDOUT_FAILURE_PATTERNS if p in clean)
        for regex in _HEALTH_FAILURE_REGEXES:
            m_fail = regex.search(clean)
            if m_fail:
                contradictions.append(m_fail.group(0).strip())

    return exit_code, passed, contradictions


def _parse_monitor_log(log_path: Path) -> list[str]:
    """Parse a monitor check log for critical/unhealthy signals.

    Returns list of concern descriptions found.
    """
    try:
        content = log_path.read_text()
    except Exception:
        return []

    concerns: list[str] = []
    clean = _ANSI_ESCAPE.sub("", content).lower()

    # Check exec_summary — this is the authoritative agent assessment.
    # The full log body is NOT checked because it frequently contains the word
    # "critical" in benign recommendation contexts (e.g., "critical API
    # endpoints") which would cause false positives.
    m = _EXEC_SUMMARY_RE.search(clean)
    if m:
        summary_text = m.group(1).strip()
        for pattern in _MONITOR_CRITICAL_PATTERNS:
            if pattern in summary_text:
                concerns.append(f"monitor summary: '{pattern}'")
                break  # One concern per summary is enough

    return concerns


def _count_trajectory_deploy_calls(traj: dict) -> int:
    """Count deployment-phase calls in a trajectory's call list."""
    count = 0
    for call in traj.get("calls", []):
        if call.get("phase") == "deployment":
            count += 1
    return count


def _is_success_status(status: str | None) -> bool:
    """Check if a trajectory status indicates success."""
    if status is None:
        return False
    normalized = status.strip().lower()
    return normalized in ("completed", "success", "succeeded", "ok", "healthy")


def _had_prior_health_failures(
    health_logs: list[Path],
    recheck_logs: list[Path],
) -> bool:
    """Check if any health check before the final one failed."""
    # If there are rechecks, earlier health checks must have failed
    if recheck_logs:
        return True

    # If there are multiple health check files, earlier ones likely failed
    if len(health_logs) > 1:
        # Verify that at least one earlier log has a non-zero exit code
        for log in health_logs[:-1]:
            try:
                content = log.read_text()
            except Exception:
                continue
            m = _HEALTH_LOG_EXIT_CODE_RE.search(content)
            if m and int(m.group(1)) != 0:
                return True

    return False
