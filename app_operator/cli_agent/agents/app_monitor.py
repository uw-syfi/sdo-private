from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from app_operator.dspy_integration import DSPyConfig
    from libs.agent_cli.base import CodingAgent

from app_operator.cli_agent.agents.context import AgentContext
from app_operator.cli_agent.agents.health_judge import AppHealthJudge
from app_operator.config import OperatorConfig
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    Phase,
    TrajectoryRecorderProtocol,
)
from app_operator.ui_protocol import NullOperatorUI, OperatorUI


# Kept importable for backward compatibility
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


class MonitoringTask(ABC):
    """Abstract base class for monitoring tasks. Kept for backward compatibility."""

    @abstractmethod
    def run(self, operator: MonitorLike) -> None:
        """Execute the monitoring task."""

    @abstractmethod
    def analyze(self, operator: MonitorLike, result: Any) -> None:
        """Use a coding agent to analyze results and provide suggestions."""


def _format_assessment_content(verdict: Any) -> str:
    """Format a HealthVerdict into assessment log text."""
    status = "healthy" if verdict.healthy else "unhealthy"
    content = (
        f"=== Health Assessment ===\n"
        f"Status: {status}\n"
        f"Script fixed: {verdict.script_was_fixed}\n\n"
        f"Assessment: {verdict.assessment}\n"
    )
    if verdict.diagnosis:
        content += f"\nDiagnosis: {verdict.diagnosis}\n"
    return content


class HealthCheckTask(MonitoringTask):
    """A monitoring task specifically for running health checks. Kept for backward compatibility."""

    def run(self, operator: MonitorLike) -> None:
        monitor = operator
        with monitor.recorder.phase(Phase.MONITORING, {"cycle": monitor.check_count}) as r:
            judge = AppHealthJudge(
                repo_path=monitor.repo_path,
                coding_agent=monitor.agent,
                health_check_script=monitor.health_check_script,
                filesystem=monitor.filesystem,
                operator_config=monitor.operator_config,
                recorder=monitor.recorder,
                dspy_config=monitor.dspy_config,
                ui=monitor.ui,
            )

            verdict = judge.assess()

            status = "healthy" if verdict.healthy else "unhealthy"
            logger.info(f"Health assessment: {status}")
            r.add_assistant_message(f"Health assessment: {status}. {verdict.assessment}")

            self._save_assessment_log(monitor, verdict)

    def analyze(self, operator: MonitorLike, result: Any) -> None:
        """No-op — analysis is now part of the agent-based assessment in run()."""

    def _save_assessment_log(self, monitor: MonitorLike, verdict: Any) -> None:
        """Write verdict to a log file."""
        try:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            monitor.filesystem.mkdir(monitor.log_dir, parents=True, exist_ok=True)
            log_file = monitor.log_dir / f"check_{monitor.check_count}_{timestamp}.log"
            with open(log_file, "w") as f:
                f.write(_format_assessment_content(verdict))
            logger.info(f"Assessment saved to: {log_file}")
        except (OSError, RuntimeError) as e:
            logger.warning(f"Failed to save assessment log: {e}")


class AppMonitor:
    """Agent responsible for monitoring application health."""

    def __init__(
        self,
        repo_path: Path,
        agent: CodingAgent,
        filesystem: FileSystemInterface | None = None,
        operator_config: OperatorConfig | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        dspy_config: DSPyConfig | None = None,
        ui: OperatorUI | None = None,
        *,
        ctx: AgentContext | None = None,
    ):
        if ctx is not None:
            self._ctx = ctx
        else:
            self._ctx = AgentContext(
                repo_path=repo_path,
                coding_agent=agent,
                filesystem=filesystem if filesystem is not None else RealFilesystem(),
                operator_config=operator_config or OperatorConfig(),
                recorder=recorder or NullTrajectoryRecorder(),
                dspy_config=dspy_config,
                ui=ui or NullOperatorUI(),
            )

        # Convenience aliases
        self.repo_path = self._ctx.repo_path
        self.agent = self._ctx.coding_agent
        self.filesystem = self._ctx.filesystem
        self.operator_config = self._ctx.operator_config
        self.recorder = self._ctx.recorder
        self.dspy_config = self._ctx.dspy_config
        self.ui = self._ctx.ui
        self.check_count = 0
        self.healthy: bool = True
        self.health_check_script = self._ctx.sds_dir / "health_check.sh"
        self.log_dir = self._ctx.sds_dir / "logs" / "monitor"
        # Keep monitoring_tasks for backward compat
        self.monitoring_tasks: list[MonitoringTask] = [HealthCheckTask()]

    def run(
        self,
        interval: int = 30,
        max_checks: int | None = None,
        check_shutdown: Callable[[], bool] | None = None,
    ):
        """Monitor application health every interval.

        Args:
            interval: Seconds between checks.
            max_checks: Maximum number of monitoring cycles. If None, runs indefinitely.
            check_shutdown: Callable returning True if shutdown requested.
        """
        logger.info(
            f"Starting application monitoring(interval: {interval}s, max_checks: {max_checks or 'unlimited'})..."
        )

        if self.filesystem.exists(self.log_dir):
            self.filesystem.remove_tree(self.log_dir)
        self.filesystem.mkdir(self.log_dir, parents=True, exist_ok=True)

        self.check_count = 0

        while not (check_shutdown and check_shutdown()):
            if max_checks is not None and self.check_count >= max_checks:
                logger.info(f"Reached maximum number of checks ({max_checks}). Stopping monitor.")
                break

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

            emit_progress("monitoring", cycle=self.check_count)
            self.ui.set_stage("Monitoring", detail=f"Cycle {self.check_count}")
            logger.info(f"Monitoring Cycle #{self.check_count}")

            self._run_health_check()

    def _run_health_check(self) -> None:
        """Run a single health check cycle using AppHealthJudge."""
        with self.recorder.phase(Phase.MONITORING, {"cycle": self.check_count}) as r:
            judge = AppHealthJudge.from_context(self._ctx)
            verdict = judge.assess()

            self.healthy = verdict.healthy
            status = "healthy" if verdict.healthy else "unhealthy"
            logger.info(f"Health assessment: {status}")
            r.add_assistant_message(f"Health assessment: {status}. {verdict.assessment}")

            self._save_assessment_log(verdict)

    def _save_assessment_log(self, verdict) -> None:
        """Write verdict to a log file."""
        try:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            self.filesystem.mkdir(self.log_dir, parents=True, exist_ok=True)
            log_file = self.log_dir / f"check_{self.check_count}_{timestamp}.log"
            with open(log_file, "w") as f:
                f.write(_format_assessment_content(verdict))
            logger.info(f"Assessment saved to: {log_file}")
        except (OSError, RuntimeError) as e:
            logger.warning(f"Failed to save assessment log: {e}")
