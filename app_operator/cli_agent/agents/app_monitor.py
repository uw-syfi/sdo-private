import time
import re
import shutil
import contextlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List

from app_operator.cli_agent.backend.base import CodingAgent
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.prompts import get_loader
from app_operator.cli_agent.healthcheck import run_health_check
from app_operator.trajectory import (
    Phase,
    TrajectoryRecorderProtocol,
    NullTrajectoryRecorder,
)


class MonitoringTask(ABC):
    """Abstract base class for monitoring tasks."""

    @abstractmethod
    def run(self, operator: Any) -> None:
        """Execute the monitoring task.

        Args:
            operator: The AppOperator/AppMonitor instance running this task.
        """
        pass

    @abstractmethod
    def analyze(self, operator: Any, result: Any) -> None:
        """Use a coding agent to analyze results and provide suggestions."""
        pass


class HealthCheckTask(MonitoringTask):
    """A monitoring task specifically for running health checks."""

    def run(self, operator: Any) -> None:
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
                monitor.repo_path, monitor.health_check_script
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

    def analyze(self, operator: Any, result: Any) -> None:
        """Analyze health check results using the agent."""
        monitor = operator
        health_result = result
        logger.info(
            f"Asking {monitor.agent.__class__.__name__} to Analyze Health Check Results"
        )

        # Prepare health check context
        context = self._prepare_health_context(health_result, monitor.check_count)

        # Create analysis prompt
        prompt = self._create_analysis_prompt(context, monitor.repo_path)

        try:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            # Ensure log directory exists
            monitor.filesystem.mkdir(monitor.log_dir, parents=True, exist_ok=True)
            log_file = monitor.log_dir / f"check_{monitor.check_count}_{timestamp}.log"

            logger.info(
                f"Consulting {monitor.agent.__class__.__name__} for health analysis..."
            )

            # Run agent and redirect its output to the log file
            with open(log_file, "w") as f:
                with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
                    response = monitor.agent.generate(
                        prompt, cwd=str(monitor.repo_path), timeout=120
                    )

                # Explicitly write the response to the log file
                f.write("\n\n=== Agent Analysis ===\n")
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

    def _create_analysis_prompt(self, context: str, repo_path: Path) -> str:
        """Create a prompt for the coding agent to analyze health check results."""
        return get_loader().render(
            "monitor/analyze_health.jinja2", repo_path=repo_path, context=context
        )


class AppMonitor:
    """Agent responsible for monitoring application health."""

    def __init__(
        self,
        repo_path: Path,
        agent: CodingAgent,
        filesystem: Optional[FileSystemInterface] = None,
        recorder: Optional[TrajectoryRecorderProtocol] = None,
    ):
        """Initialize the monitor agent.

        Args:
            repo_path: Path to the repository.
            agent: The coding agent to use for analysis.
            filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
            recorder: Trajectory recorder instance.
        """
        self.repo_path = repo_path
        self.agent = agent
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.recorder = recorder or NullTrajectoryRecorder()
        self.monitoring_tasks: List[MonitoringTask] = [HealthCheckTask()]
        self.check_count = 0
        self.health_check_script = self.repo_path / ".sds" / "health_check.sh"
        self.log_dir = self.repo_path / ".sds" / "logs" / "monitor"

    def run(
        self,
        interval: int = 30,
        max_checks: Optional[int] = None,
        check_shutdown: Optional[Callable[[], bool]] = None,
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
        if self.log_dir.exists():
            shutil.rmtree(self.log_dir)
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

            logger.info(f"Monitoring Cycle #{self.check_count}")

            # Run all registered monitoring tasks
            for task in self.monitoring_tasks:
                task.run(self)
