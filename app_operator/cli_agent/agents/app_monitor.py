from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from app_operator.cli_agent.agents.context import AgentContext
    from app_operator.dspy_integration import DSPyConfig
    from app_operator.types import CommandResult, HealthVerdict
    from libs.agent_cli.base import CodingAgent

from app_operator.cli_agent.agents.health_judge import AppHealthJudge
from app_operator.config import DeploymentConfig, OperatorConfig
from app_operator.exceptions import AgentError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.healthcheck import parse_health_check_log, run_health_check
from app_operator.logger import logger
from app_operator.prompts import get_loader
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    Phase,
    TrajectoryRecorderProtocol,
)
from app_operator.ui_protocol import NullOperatorUI, OperatorUI

HEALTH_OUTPUT_MAX_LENGTH = 5000  # characters before truncating health check stdout
HEALTH_OUTPUT_TRUNCATE_AT = 2000  # characters to keep when truncating health check stderr
_EXEC_SUMMARY_RE = re.compile(r"<exec_summary>(.*?)</exec_summary>", re.DOTALL | re.IGNORECASE)
_SUMMARY_HEALTHY_HINTS = (
    "currently healthy",
    "all services are operational",
    "all services are running",
    "fully operational",
    "system is stable",
    "100% healthy",
)
_SUMMARY_UNHEALTHY_HINTS = (
    "critical state",
    "unhealthy",
    "not healthy",
    "services failing",
    "services are failing",
    "services down",
    "not running",
    "degraded",
)


class MonitorLike(Protocol):
    """Protocol describing the monitor interface used by MonitoringTask."""

    repo_path: Path
    agent: CodingAgent
    filesystem: FileSystemInterface
    recorder: TrajectoryRecorderProtocol
    dspy_config: DSPyConfig | None
    ui: OperatorUI
    check_count: int
    health_check_script: Path
    log_dir: Path
    operator_config: OperatorConfig
    deployment_config: DeploymentConfig


class MonitoringTask(ABC):
    """Abstract base class for monitoring tasks."""

    @abstractmethod
    def run(self, operator: MonitorLike) -> bool:
        """Execute the monitoring task.

        Args:
            operator: The AppOperator/AppMonitor instance running this task.

        Returns:
            True if the check passed, False otherwise.
        """

    @abstractmethod
    def analyze(self, operator: MonitorLike, result: CommandResult, health_log_path: Path | None = None) -> None:
        """Use a coding agent to analyze results and provide suggestions."""


class HealthCheckTask(MonitoringTask):
    """A monitoring task specifically for running health checks."""

    def _assess_with_health_judge(
        self,
        monitor: MonitorLike,
        recorder: TrajectoryRecorderProtocol,
    ) -> HealthVerdict:
        """Run the health judge agent for the current monitoring cycle."""
        judge = AppHealthJudge(
            repo_path=monitor.repo_path,
            coding_agent=monitor.agent,
            health_check_script=monitor.health_check_script,
            filesystem=monitor.filesystem,
            operator_config=monitor.operator_config,
            recorder=recorder,
            dspy_config=monitor.dspy_config,
            ui=monitor.ui,
            deployment_config=monitor.deployment_config,
        )
        return judge.assess()

    def run(self, operator: MonitorLike) -> bool:
        """Run the health check task.

        Args:
            operator: The AppMonitor instance.

        Returns:
            True if the health check passed, False otherwise.
        """
        monitor = operator
        # Start monitoring phase in trajectory
        with monitor.recorder.phase(Phase.MONITORING, {"cycle": monitor.check_count}) as r:
            health_log_path = monitor.log_dir / f"health_check_cycle_{monitor.check_count}.log"
            # Run health check
            start_time = time.time()
            runtime_health_result = run_health_check(
                monitor.repo_path,
                monitor.health_check_script,
                log_file_path=health_log_path,
                ui=monitor.ui,
            )
            duration = time.time() - start_time
            health_result: CommandResult = runtime_health_result

            canonical_health_result = parse_health_check_log(health_log_path)
            if canonical_health_result is None:
                warning = (
                    f"Could not parse canonical health log at {health_log_path}. "
                    "Falling back to in-memory health result."
                )
                logger.warning(warning)
                r.add_assistant_message(warning)
            else:
                if self._health_result_mismatch(runtime_health_result, canonical_health_result):
                    warning = (
                        f"Runtime health result differed from canonical log at {health_log_path}. "
                        "Using canonical log content for validation/analysis."
                    )
                    logger.warning(warning)
                    r.add_assistant_message(warning)
                health_result = canonical_health_result

            # Record health check tool call
            r.add_tool_call(
                tool="bash",
                args={"script": ".sds/health_check.sh"},
                stdout=health_result["stdout"],
                stderr=health_result["stderr"],
                exit_code=health_result["exit_code"],
                duration=duration,
            )

            health_verdict = self._assess_with_health_judge(monitor, r)
            verdict = "healthy" if health_verdict.healthy else "unhealthy"
            r.add_assistant_message(
                f"Health judge verdict: {verdict} - {health_verdict.diagnosis or health_verdict.assessment}"
            )

            effective_health: CommandResult = {
                "success": health_verdict.healthy,
                "exit_code": 0 if health_verdict.healthy else health_result["exit_code"],
                "stdout": health_result["stdout"],
                "stderr": health_result["stderr"],
            }
            if not health_verdict.healthy:
                effective_health["stderr"] = (
                    ((health_result["stderr"].rstrip() + "\n") if health_result["stderr"] else "")
                    + "Health judge marked application unhealthy: "
                    + (health_verdict.diagnosis or health_verdict.assessment)
                )

            self.analyze(monitor, effective_health, health_log_path=health_log_path)

            if not effective_health["success"]:
                r.set_phase_status("failed")

            return bool(effective_health["success"])

    def analyze(self, operator: MonitorLike, result: CommandResult, health_log_path: Path | None = None) -> None:
        """Analyze health check results using the agent."""
        monitor = operator
        health_result = result
        logger.info(f"Asking {monitor.agent.__class__.__name__} to Analyze Health Check Results")

        # Prepare health check context
        context = self._prepare_health_context(
            health_result,
            monitor.check_count,
            health_log_path=health_log_path,
        )

        # Create analysis prompt
        prompt = self._create_analysis_prompt(
            context,
            monitor.repo_path,
            health_result,
            monitor.check_count,
            monitor.dspy_config,
            recorder=monitor.recorder,
        )

        try:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            # Ensure log directory exists
            monitor.filesystem.mkdir(monitor.log_dir, parents=True, exist_ok=True)
            log_file = monitor.log_dir / f"check_{monitor.check_count}_{timestamp}.log"

            logger.info(f"Consulting {monitor.agent.__class__.__name__} for health analysis...")

            response = monitor.agent.generate(
                prompt,
                cwd=str(monitor.repo_path),
                timeout=monitor.operator_config.agent_timeout,
            )

            consistency_warning = self._build_summary_consistency_warning(
                response=response, health_result=health_result
            )

            # Write the response to the log file
            with open(log_file, "w") as f:
                f.write("=== Agent Analysis ===\n")
                if consistency_warning:
                    f.write("=== MONITOR_VERDICT_MISMATCH ===\n")
                    f.write(consistency_warning)
                    f.write("\n\n")
                f.write(response)

            if consistency_warning:
                logger.warning(consistency_warning)
                monitor.recorder.add_assistant_message(consistency_warning)

            # Extract executive summary
            match = _EXEC_SUMMARY_RE.search(response)
            if match:
                summary = match.group(1).strip()
                logger.info(f"Summary: {summary}")
            else:
                logger.warning("Summary not found in expected XML format. See log for full analysis.")

            logger.info(f"Full analysis saved to: {log_file}")

            # End the monitoring phase (handled by context manager exit, defaulting to success)

        except (AgentError, OSError, RuntimeError) as e:
            logger.error(f"Agent analysis failed: {e}")
            monitor.recorder.add_assistant_message(f"Analysis failed: {e}")
            # Ensure we mark phase as failed
            monitor.recorder.set_phase_status("failed")

    def _prepare_health_context(
        self,
        health_result: CommandResult,
        check_count: int,
        health_log_path: Path | None = None,
    ) -> str:
        """Prepare health check context for analysis."""
        context_parts = []

        context_parts.append(f"## Health Check #{check_count}")
        if health_log_path is not None:
            context_parts.append(f"Canonical Log Path: {health_log_path}")
        context_parts.append(f"Exit Code: {health_result['exit_code']}")
        context_parts.append(f"Status: {'PASSED' if health_result['success'] else 'FAILED'}")
        context_parts.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")

        if health_result["stdout"]:
            context_parts.append("\n### Output:")
            stdout = health_result["stdout"]
            # For health checks, include more output (up to HEALTH_OUTPUT_MAX_LENGTH chars)
            if len(stdout) > HEALTH_OUTPUT_MAX_LENGTH:
                stdout = stdout[-HEALTH_OUTPUT_MAX_LENGTH:]
                context_parts.append(f"... (truncated, showing last {HEALTH_OUTPUT_MAX_LENGTH} chars)")
            context_parts.append(stdout)

        if health_result["stderr"]:
            context_parts.append("\n### Errors:")
            stderr = health_result["stderr"]
            if len(stderr) > HEALTH_OUTPUT_TRUNCATE_AT:
                stderr = stderr[-HEALTH_OUTPUT_TRUNCATE_AT:]
                context_parts.append(f"... (truncated, showing last {HEALTH_OUTPUT_TRUNCATE_AT} chars)")
            context_parts.append(stderr)

        return "\n".join(context_parts)

    def _create_analysis_prompt(
        self,
        context: str,
        repo_path: Path,
        health_result: CommandResult,
        check_count: int,
        dspy_config: DSPyConfig | None = None,
        recorder=None,
    ) -> str:
        """Create a prompt for the coding agent to analyze health check results.

        Args:
            context: Formatted health check context (for Jinja2)
            repo_path: Repository path
            health_result: Raw health check result dict
            check_count: Current monitoring iteration
            dspy_config: Optional DSPy configuration
            recorder: Optional trajectory recorder for kwargs capture

        Returns:
            Rendered prompt string
        """
        # Pass both Jinja2 fields (context, repo_path) and DSPy fields
        # (health_check_output, exit_code, iteration) to support both renderers
        prompt = get_loader(dspy_config).render(
            "monitor/analyze_health.jinja2",
            # Jinja2 fields (for backward compatibility)
            repo_path=repo_path,
            context=context,
            # DSPy fields (for DSPy signature)
            health_check_output=health_result["stdout"],
            exit_code=health_result["exit_code"],
            iteration=check_count,
            recorder=recorder,
        )

        return prompt

    def _health_result_mismatch(self, runtime_result: CommandResult, canonical_result: CommandResult) -> bool:
        """Return True when canonical log output differs from runtime output."""
        runtime_exit = runtime_result["exit_code"]
        canonical_exit = canonical_result["exit_code"]
        runtime_success = runtime_result["success"]
        canonical_success = canonical_result["success"]
        runtime_stdout = runtime_result["stdout"].strip()
        canonical_stdout = canonical_result["stdout"].strip()
        runtime_stderr = runtime_result["stderr"].strip()
        canonical_stderr = canonical_result["stderr"].strip()

        return (
            runtime_exit != canonical_exit
            or runtime_success != canonical_success
            or runtime_stdout != canonical_stdout
            or runtime_stderr != canonical_stderr
        )

    def _build_summary_consistency_warning(self, response: str, health_result: CommandResult) -> str | None:
        """Build a warning when monitor summary disagrees with health status."""
        inferred = self._infer_exec_summary_verdict(response)
        if inferred is None:
            return None

        expected = "healthy" if health_result["success"] else "unhealthy"
        if inferred == expected:
            return None

        exit_code = health_result["exit_code"]
        return (
            "MONITOR_VERDICT_MISMATCH: Monitor exec_summary implies "
            f"{inferred.upper()} but canonical health result is {expected.upper()} "
            f"(exit_code={exit_code}). Treating analysis verdict as untrusted for this cycle."
        )

    def _infer_exec_summary_verdict(self, response: str) -> str | None:
        """Infer healthy/unhealthy from exec_summary text when unambiguous."""
        match = _EXEC_SUMMARY_RE.search(str(response or ""))
        if not match:
            return None

        summary = match.group(1).lower()
        healthy_hits = [hint for hint in _SUMMARY_HEALTHY_HINTS if hint in summary]
        unhealthy_hits = [hint for hint in _SUMMARY_UNHEALTHY_HINTS if hint in summary]

        if healthy_hits and not unhealthy_hits:
            return "healthy"
        if unhealthy_hits and not healthy_hits:
            return "unhealthy"
        return None


class AppMonitor:
    """Agent responsible for monitoring application health."""

    def __init__(
        self,
        repo_path: Path,
        agent: CodingAgent,
        filesystem: FileSystemInterface | None = None,
        deployment_config: DeploymentConfig | None = None,
        operator_config: OperatorConfig | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        dspy_config: DSPyConfig | None = None,
        ui: OperatorUI | None = None,
        ctx: AgentContext | None = None,
    ):
        """Initialize the monitor agent.

        Args:
            repo_path: Path to the repository.
            agent: The coding agent to use for analysis.
            filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
            deployment_config: Optional deployment configuration.
            operator_config: Optional operator configuration for timeouts.
            recorder: Trajectory recorder instance.
            dspy_config: Optional DSPy configuration for optimized prompts.
            ui: Optional UI interface.
        """
        if ctx is not None:
            filesystem = filesystem if filesystem is not None else ctx.filesystem
            operator_config = operator_config if operator_config is not None else ctx.operator_config
            recorder = recorder if recorder is not None else ctx.recorder
            dspy_config = dspy_config if dspy_config is not None else ctx.dspy_config
            ui = ui if ui is not None else ctx.ui

        self.repo_path = repo_path
        self.agent = agent
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.deployment_config = deployment_config or DeploymentConfig()
        self.operator_config = operator_config or OperatorConfig()
        self.recorder = recorder or NullTrajectoryRecorder()
        self.dspy_config = dspy_config
        self.ui = ui or NullOperatorUI()
        self.monitoring_tasks: list[MonitoringTask] = [HealthCheckTask()]
        self.check_count = 0
        self.healthy: bool = True
        self.health_check_script = self.repo_path / ".sds" / "health_check.sh"
        self.log_dir = self.repo_path / ".sds" / "logs" / "monitor"

    def run(
        self,
        interval: int = 30,
        max_checks: int | None = None,
        check_shutdown: Callable[[], bool] | None = None,
    ):
        """Monitor application health and provide agent analysis every interval.

        After this method returns, ``self.healthy`` indicates whether the
        most recent monitoring cycle passed all checks.

        Args:
            interval: Seconds between checks.
            max_checks: Maximum number of monitoring cycles. If None, runs indefinitely.
            check_shutdown: Callable returning True if shutdown requested.
        """
        logger.info(
            f"Starting application monitoring(interval: {interval}s, max_checks: {max_checks or 'unlimited'})..."
        )

        # Clear log directory on startup
        if self.filesystem.exists(self.log_dir):
            self.filesystem.remove_tree(self.log_dir)
        self.filesystem.mkdir(self.log_dir, parents=True, exist_ok=True)

        self.check_count = 0
        self.healthy = True

        while not (check_shutdown and check_shutdown()):
            if max_checks is not None and self.check_count >= max_checks:
                logger.info(f"Reached maximum number of checks ({max_checks}). Stopping monitor.")
                break

            # Wait for interval
            waited = 0.0
            step = 1.0
            while waited < interval:
                if check_shutdown and check_shutdown():
                    return

                remaining = interval - waited
                current_step = min(step, remaining)

                time.sleep(current_step)
                waited += current_step

            self.check_count += 1

            self.ui.set_stage("Monitoring", detail=f"Cycle {self.check_count}")
            logger.info(f"Monitoring Cycle #{self.check_count}")

            # Run all registered monitoring tasks
            cycle_healthy = True
            for task in self.monitoring_tasks:
                if not task.run(self):
                    cycle_healthy = False
            self.healthy = cycle_healthy
