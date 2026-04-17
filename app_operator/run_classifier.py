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
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

_HEALTH_LOG_EXIT_CODE_RE = re.compile(r"^Exit Code:\s*(-?\d+)", re.MULTILINE)
_HEALTH_LOG_VALIDATION_RE = re.compile(r"^Validation:\s*(PASSED|FAILED)", re.MULTILINE)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_HEALTH_LOG_TOTAL_CHECKS_RE = re.compile(r"^Total Checks:\s*(\d+)", re.MULTILINE)

# Detect warning_check() used for HTTP endpoint probes — a hard check downgraded to a
# warning.  The pattern matches any non-comment shell line that calls warning_check and
# also includes a curl/wget probe targeting localhost.
_WARNING_CHECK_ENDPOINT_RE = re.compile(
    r"^\s*warning_check\b[^\n]*(?:curl|wget)\b[^\n]*https?://localhost",
    re.MULTILINE,
)
_SKIPPED_CRITICAL_CHECK_RE = re.compile(
    r"skip(?:ping)?\s+.*(?:data-tier|data tier|database|cache|mongodb|memcached).*(?:check|probe)",
    re.IGNORECASE,
)
_REMOVED_CRITICAL_CHECK_RE = re.compile(
    r"removed.*(?:data-tier|data tier|database|cache|mongodb|memcached).*(?:check|probe)",
    re.IGNORECASE,
)

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
    # Use regex to avoid matching "non-critical" or "critical: none"
    re.compile(r"(?<!non-)critical(?!\s*:\s*none)", re.IGNORECASE),
    re.compile(r"\bunhealthy\b", re.IGNORECASE),
    re.compile(r"\bnot healthy\b", re.IGNORECASE),
    re.compile(r"\bnot running\b", re.IGNORECASE),
    re.compile(r"\bservice is down\b", re.IGNORECASE),
    re.compile(r"\binaccessible\b", re.IGNORECASE),
    re.compile(r"\boutage\b", re.IGNORECASE),
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
    health_contradictions: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    monitor_concerns: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    reasons: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    health_check_coverage_dropped: bool = False


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
        metadata = traj.get("metadata", {})
        if isinstance(metadata, dict):
            metadata_dict = cast("dict[str, Any]", metadata)
            result.trajectory_status = str(metadata_dict.get("status", "unknown"))
        else:
            result.trajectory_status = "unknown"
        deployment = traj.get("deployment", [])
        if isinstance(deployment, list):
            result.trajectory_attempts = len(cast("list[Any]", deployment))
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
    # Sum deployment entries across ALL trajectory files for this run.
    # The operator may restart mid-deployment and create a new trajectory file,
    # so the latest trajectory alone may not account for all deploy_attempt_*.log files.
    traj_deploy_call_count = _count_trajectory_deploy_calls(run_dir)

    # --- 6. Apply classification rules ---
    is_traj_success = _is_success_status(result.trajectory_status)

    if not is_traj_success:
        if result.final_health_passed:
            if result.trajectory_status is None:
                # Trajectory couldn't be loaded (disk full, corrupt, missing)
                # but health check passed — deployment actually succeeded.
                result.label = "recovered_success"
                result.reasons.append(
                    "Health check passed but trajectory data unavailable (possibly due to disk space or write failure)"
                )
            elif result.health_contradictions:
                # Health check reports exit 0 but stdout contains failure signals,
                # AND trajectory says failed — the deployment actually failed; the
                # health check is a false positive.
                result.label = "false_positive"
                result.reasons.append(
                    "Health check exit code 0 but stdout contains failure signals: "
                    + "; ".join(result.health_contradictions)
                )
            else:
                # Trajectory says failed but health check passed cleanly with no
                # contradictions.  Check whether the health check coverage regressed
                # (i.e. the agent weakened the script rather than fixing the app).
                health_script = run_dir / ".sds" / "health_check.sh"
                if _health_check_coverage_dropped(health_logs, recheck_logs, health_script):
                    result.label = "health_policy_regression"
                    result.health_check_coverage_dropped = True
                    result.reasons.append(
                        "Health check coverage regressed: checks were removed or downgraded "
                        "to warnings, masking the underlying failure"
                    )
                else:
                    result.label = "recovered_success"
                    result.reasons.append(
                        f"Trajectory status '{result.trajectory_status}' indicates failure "
                        "but final health check passed cleanly; treating as recovered success"
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

    health_script = run_dir / ".sds" / "health_check.sh"
    if _health_check_coverage_dropped(health_logs, recheck_logs, health_script):
        result.label = "health_policy_regression"
        result.health_check_coverage_dropped = True
        result.reasons.append(
            "Health check coverage regressed: checks were removed, skipped, or downgraded "
            "to warnings instead of validating the underlying deployment"
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
    results: list[RunClassification] = []
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


def _load_trajectory(run_dir: Path) -> dict[str, Any] | None:
    """Load the most recent trajectory JSON from a run directory."""
    traj_dir = run_dir / ".sds" / "trajectories"
    if not traj_dir.exists():
        return None
    traj_files = sorted(traj_dir.glob("trajectory_*.json"), reverse=True)
    if not traj_files:
        return None
    try:
        parsed = json.loads(traj_files[0].read_text())
        if isinstance(parsed, dict):
            return cast("dict[str, Any]", parsed)
        return None
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

    # Prefer the explicit validation verdict written by validate_health_check_result
    # over the raw exit code — the validator applies heuristic + agent checks that
    # the raw exit code alone cannot capture (e.g. exit 0 with inconsistent output).
    validation_match = _HEALTH_LOG_VALIDATION_RE.search(content)
    if validation_match:
        passed = validation_match.group(1) == "PASSED"
    else:
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
            if pattern.search(summary_text):
                concerns.append(f"monitor summary: '{pattern.pattern}'")
                break  # One concern per summary is enough

    return concerns


def _count_trajectory_deploy_calls(run_dir: Path) -> int:
    """Count total deployment invocations across all trajectory files for a run.

    When the operator restarts mid-deployment it creates a new trajectory file,
    so the latest file alone may under-count attempts recorded in earlier files.
    Summing ``deployment[]`` lengths across all files gives the true total, which
    should equal the number of ``deploy_attempt_*.log`` files on disk.
    """
    traj_dir = run_dir / ".sds" / "trajectories"
    if not traj_dir.exists():
        return 0
    total = 0
    for traj_file in traj_dir.glob("trajectory_*.json"):
        try:
            traj = json.loads(traj_file.read_text())
            total += len(traj.get("deployment", []))
        except Exception:
            pass
    return total


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


def _extract_total_checks(log_path: Path) -> int | None:
    """Extract the 'Total Checks' count from a health check log."""
    try:
        m = _HEALTH_LOG_TOTAL_CHECKS_RE.search(log_path.read_text())
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _health_check_script_weakened(health_script: Path) -> bool:
    """Return True when health_check.sh shows static signs of weakening.

    Detects the pattern where HTTP endpoint probes (curl/wget to localhost)
    are silently downgraded from hard failures (``check``) to advisory
    warnings (``warning_check``), so a failing endpoint no longer causes the
    health check to exit non-zero.
    """
    if not health_script.exists():
        return False
    try:
        content = health_script.read_text()
    except Exception:
        return False
    return bool(
        _WARNING_CHECK_ENDPOINT_RE.search(content)
        or _SKIPPED_CRITICAL_CHECK_RE.search(content)
        or _REMOVED_CRITICAL_CHECK_RE.search(content)
    )


def _health_check_coverage_dropped(
    health_logs: list[Path],
    recheck_logs: list[Path],
    health_script: Path,
) -> bool:
    """Return True if health check coverage regressed during this run.

    Two independent signals are checked:

    1. **Check-count drop**: When there are multiple health check log files
       (including rechecks) the total-checks counter in the first log is
       compared with the final one.  A drop of more than 15 % indicates that
       checks were removed between attempts.

    2. **Static script analysis**: If health_check.sh uses ``warning_check``
       for HTTP endpoint probes (curl/wget to localhost) those hard failures
       have been silently downgraded to warnings — a clear sign of weakening.

    3. **Explicit critical-check skipping**: If the script removes or skips
       data-tier connectivity probes (database/cache) and replaces them with
       container-status-only checks, the run must not count as a trustworthy
       success.
    """
    # Signal 1: count drop across health/recheck logs
    all_logs = list(health_logs) + list(recheck_logs)
    if len(all_logs) >= 2:
        first_count = _extract_total_checks(all_logs[0])
        last_count = _extract_total_checks(all_logs[-1])
        if first_count is not None and last_count is not None and first_count > 0 and last_count < first_count * 0.85:
            return True

    # Signal 2: static analysis of the final health_check.sh
    return bool(_health_check_script_weakened(health_script))
