from __future__ import annotations

import subprocess
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from app_operator.cli_agent.agents.context import AgentContext
    from app_operator.types import CommandResult

from app_operator.cli_agent._progress_summarizer import ProgressSummarizer
from app_operator.logger import logger
from app_operator.subprocess_runner import SubprocessRunner

MONITOR_INITIAL_DELAY_SECS = 15.0
MONITOR_SUMMARY_INTERVAL_SECS = 30.0


class DeployExecutor:
    """Wraps deployment script execution with progress monitoring."""

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx
        self.deploy_script = ctx.sds_dir / "deploy.sh"

    def _get_time(self) -> float:
        """Get current time. Separate method to allow mocking in tests."""
        return time.time()

    def _sleep(self, seconds: float) -> None:
        """Sleep for given seconds. Separate method to allow mocking in tests."""
        time.sleep(seconds)

    def run_deploy_command(
        self,
        command: str = "start",
        timeout: int | None = None,
        log_file_path: Path | None = None,
        check_shutdown: Callable[[], bool] | None = None,
    ) -> CommandResult:
        """Run the deployment script with a specific command.

        Args:
            command: The command to pass to the script (e.g., "start", "stop").
            timeout: Timeout in seconds. If None, uses operator_config.deploy_timeout.
            log_file_path: Optional path to write output logs to.
            check_shutdown: Optional callable returning True if shutdown requested.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        if timeout is None:
            timeout = self.ctx.operator_config.deploy_timeout
        logger.info(f"Running deployment script: {self.deploy_script} {command}")

        if log_file_path:
            logger.info(f"Logging output to: {log_file_path}")

        runner = SubprocessRunner(
            command=[str(self.deploy_script), command],
            cwd=str(self.ctx.repo_path),
            timeout=timeout,
            log_file_path=log_file_path,
            check_shutdown=check_shutdown,
            time_func=self._get_time,
            sleep_func=self._sleep,
            popen_func=subprocess.Popen,
            ui=self.ctx.ui,
            tool_name="deploy.sh",
            tool_args={"command": command},
        )

        summarizer = ProgressSummarizer(
            agent_generate_fn=lambda prompt, silent, timeout: self.ctx.coding_agent.generate(
                prompt, silent=silent, timeout=timeout
            ),
            initial_delay=MONITOR_INITIAL_DELAY_SECS,
            summary_interval=MONITOR_SUMMARY_INTERVAL_SECS,
            time_func=self._get_time,
            recorder=self.ctx.recorder,
        )

        result = runner.run_with_progress_monitoring(summarizer)

        status = "SUCCESS" if result.get("success") else "FAILED"
        exit_code = result.get("exit_code", -1)
        logger.info(f"Deployment command '{command}' finished: {status} (Exit Code: {exit_code})")

        return result
