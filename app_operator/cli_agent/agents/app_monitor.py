from __future__ import annotations

import time
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from app_operator.dspy_integration.config import DSPyConfig

from app_operator.ui import OperatorUI, NullOperatorUI
from libs.agent_cli.base import CodingAgent
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.prompts import get_loader
from app_operator.config import OperatorConfig
from app_operator.cli_agent.healthcheck import run_health_check
from app_operator.trajectory import (
    Phase,
    TrajectoryRecorderProtocol,
    NullTrajectoryRecorder,
)


class MonitorLike(Protocol):
    """Protocol describing the monitor interface used by MonitoringTask."""

    repo_path: Path
    agent: CodingAgent
    filesystem: FileSystemInterface
    recorder: TrajectoryRecorderProtocol
    dspy_config: "DSPyConfig" | None
    ui: OperatorUI
    check_count: int
    health_check_script: Path
    log_dir: Path
    operator_config: OperatorConfig


class MonitoringTask(ABC):
    """Abstract base class for monitoring tasks."""

    @abstractmethod
    def run(self, operator: MonitorLike) -> None:
        """Execute the monitoring task.

        Args:
            operator: The AppOperator/AppMonitor instance running this task.
        """
        pass

    @abstractmethod
    def analyze(self, operator: MonitorLike, result: Any) -> None:
        """Use a coding agent to analyze results and provide suggestions."""
        pass


class HealthCheckTask(MonitoringTask):
    """A monitoring task specifically for running health checks."""

    def run(self, operator: MonitorLike) -> None:
        """Run the health check task.

        Args:
            operator: The AppMonitor instance.
        """
        monitor = operator
        # Start monitoring phase in trajectory
        with monitor.recorder.phase(
            Phase.MONITORING, {"cycle": monitor.check_count}
        ) as r:
            # Run health check
            start_time = time.time()
            health_result = run_health_check(
                monitor.repo_path,
                monitor.health_check_script,
                ui=monitor.ui,
            )
            duration = time.time() - start_time

            # Record health check tool call
            r.add_tool_call(
                tool="bash",
                args={"script": ".sds/health_check.sh"},
                stdout=health_result.get("stdout", ""),
                stderr=health_result.get("stderr", ""),
                exit_code=int(health_result.get("exit_code", -1) or -1),
                duration=duration,
            )

            self.analyze(monitor, health_result)

    def analyze(self, operator: MonitorLike, result: Any) -> None:
        """Analyze health check results using the agent."""
        monitor = operator
        health_result = result
        logger.info(
            f"Asking {monitor.agent.__class__.__name__} to Analyze Health Check Results"
        )

        # Prepare health check context
        context = self._prepare_health_context(health_result, monitor.check_count)

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

            logger.info(
                f"Consulting {monitor.agent.__class__.__name__} for health analysis..."
            )

            response = monitor.agent.generate(
                prompt,
                cwd=str(monitor.repo_path),
                timeout=monitor.operator_config.agent_timeout,
            )

            # Write the response to the log file
            with open(log_file, "w") as f:
                f.write("=== Agent Analysis ===\n")
                f.write(response)

            # Extract executive summary
            match = re.search(
                r"<exec_summary>(.*?)</exec_summary>", response, re.DOTALL
            )
            if match:
                summary = match.group(1).strip()
                logger.info(f"Summary: {summary}")
            else:
                logger.warning(
                    "Summary not found in expected XML format. See log for full analysis."
                )

            logger.info(f"Full analysis saved to: {log_file}")

            # End the monitoring phase (handled by context manager exit, defaulting to success)

        except Exception as e:
            logger.error(f"Agent analysis failed: {e}")
            monitor.recorder.add_assistant_message(f"Analysis failed: {e}")
            # Ensure we mark phase as failed
            monitor.recorder.set_phase_status("failed")

    def _prepare_health_context(self, health_result: dict, check_count: int) -> str:
        """Prepare health check context for analysis."""
        context_parts = []

        context_parts.append(f"## Health Check #{check_count}")
        context_parts.append(f"Exit Code: {health_result['exit_code']}")
        context_parts.append(
            f"Status: {'PASSED' if health_result['success'] else 'FAILED'}"
        )
        context_parts.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")

        if health_result["stdout"]:
            context_parts.append("\n### Output:")
            stdout = health_result["stdout"]
            # For health checks, include more output (up to 5000 chars)
            if len(stdout) > 5000:
                stdout = stdout[-5000:]
                context_parts.append("... (truncated, showing last 5000 chars)")
            context_parts.append(stdout)

        if health_result["stderr"]:
            context_parts.append("\n### Errors:")
            stderr = health_result["stderr"]
            if len(stderr) > 2000:
                stderr = stderr[-2000:]
                context_parts.append("... (truncated, showing last 2000 chars)")
            context_parts.append(stderr)

        return "\n".join(context_parts)

    def _create_analysis_prompt(
        self,
        context: str,
        repo_path: Path,
        health_result: dict,
        check_count: int,
        dspy_config: "DSPyConfig" | None = None,
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
            health_check_output=health_result.get("stdout", ""),
            exit_code=health_result.get("exit_code", -1),
            iteration=check_count,
            recorder=recorder,
        )

        return prompt


class AppMonitor:
    """Agent responsible for monitoring application health."""

    def __init__(
        self,
        repo_path: Path,
        agent: CodingAgent,
        filesystem: FileSystemInterface | None = None,
        operator_config: OperatorConfig | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        dspy_config: "DSPyConfig" | None = None,
        ui: OperatorUI | None = None,
    ):
        """Initialize the monitor agent.

        Args:
            repo_path: Path to the repository.
            agent: The coding agent to use for analysis.
            filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
            operator_config: Optional operator configuration for timeouts.
            recorder: Trajectory recorder instance.
            dspy_config: Optional DSPy configuration for optimized prompts.
            ui: Optional UI interface.
        """
        self.repo_path = repo_path
        self.agent = agent
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.operator_config = operator_config or OperatorConfig()
        self.recorder = recorder or NullTrajectoryRecorder()
        self.dspy_config = dspy_config
        self.ui = ui or NullOperatorUI()
        self.monitoring_tasks: list[MonitoringTask] = [HealthCheckTask()]
        self.check_count = 0
        self.health_check_script = self.repo_path / ".sds" / "health_check.sh"
        self.log_dir = self.repo_path / ".sds" / "logs" / "monitor"

    def run(
        self,
        interval: int = 30,
        max_checks: int | None = None,
        check_shutdown: Callable[[], bool] | None = None,
    ):
        """Monitor application health and provide agent analysis every interval.

        Args:
            interval: Seconds between checks.
            max_checks: Maximum number of monitoring cycles. If None, runs indefinitely.
            check_shutdown: Callable returning True if shutdown requested.
        """
        logger.info(
            f"Starting application monitoring(interval: {interval}s, max_checks: "
            f"{max_checks if max_checks else 'unlimited'})..."
        )

        # Clear log directory on startup
        if self.filesystem.exists(self.log_dir):
            self.filesystem.remove_tree(self.log_dir)
        self.filesystem.mkdir(self.log_dir, parents=True, exist_ok=True)

        self.check_count = 0

        while not (check_shutdown and check_shutdown()):
            if max_checks is not None and self.check_count >= max_checks:
                logger.info(
                    f"Reached maximum number of checks ({max_checks}). Stopping monitor."
                )
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
            for task in self.monitoring_tasks:
                task.run(self)
