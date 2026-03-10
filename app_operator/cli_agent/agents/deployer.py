from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from app_operator.dspy_integration import DSPyConfig
    from app_operator.types import CommandResult
    from libs.agent_cli.base import CodingAgent

from app_operator.cli_agent.agents.context import AgentContext
from app_operator.cli_agent.agents.deploy_executor import DeployExecutor
from app_operator.cli_agent.agents.health_judge import AppHealthJudge, HealthVerdict
from app_operator.cli_agent.agents.repair_agent import RepairAgent
from app_operator.cli_agent.agents.script_generator_agent import (
    ScriptGeneratorAgent,
    generate_scripts,  # noqa: F401 — re-exported for backward compat
)
from app_operator.config import DeploymentConfig, OperatorConfig
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    Phase,
    TrajectoryRecorderProtocol,
)
from app_operator.ui_protocol import NullOperatorUI, OperatorUI


class DeploymentAgent:
    """Orchestrates the deploy-check-fix loop by composing focused agents."""

    def __init__(
        self,
        repo_path: Path,
        coding_agent: CodingAgent,
        filesystem: FileSystemInterface | None = None,
        deployment_config: DeploymentConfig | None = None,
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
                coding_agent=coding_agent,
                filesystem=filesystem if filesystem is not None else RealFilesystem(),
                operator_config=operator_config or OperatorConfig(),
                recorder=recorder or NullTrajectoryRecorder(),
                dspy_config=dspy_config,
                ui=ui or NullOperatorUI(),
            )

        self.deployment_config = deployment_config or DeploymentConfig()

        # Convenience aliases for backward compat and internal use
        self.repo_path = self._ctx.repo_path
        self.agent = self._ctx.coding_agent
        self.filesystem = self._ctx.filesystem
        self.operator_config = self._ctx.operator_config
        self.recorder = self._ctx.recorder
        self.dspy_config = self._ctx.dspy_config
        self.ui = self._ctx.ui
        self.sds_dir = self._ctx.sds_dir
        self.deploy_script = self.sds_dir / "deploy.sh"
        self.health_check_script = self.sds_dir / "health_check.sh"

        # Composed agents
        self._executor = DeployExecutor(self._ctx)
        self._repair = RepairAgent(self._ctx, deployment_config=self.deployment_config)
        self._script_gen = ScriptGeneratorAgent(self._ctx, deployment_config=self.deployment_config)

    def _get_next_attempt_number(self) -> int:
        """Determine the next attempt number based on existing logs."""
        logs_dir = self.sds_dir / "logs"
        if not self.filesystem.exists(logs_dir):
            return 1

        log_files = self.filesystem.glob(logs_dir, "deploy_attempt_*.log")
        if not log_files:
            return 1

        max_attempt = 0
        for log_file in log_files:
            try:
                name = log_file.stem
                parts = name.split("_")
                if len(parts) >= 3 and parts[-1].isdigit():
                    num = int(parts[-1])
                    if num > max_attempt:
                        max_attempt = num
            except ValueError:
                continue

        return max_attempt + 1

    def run(self, max_attempts: int = 5, check_shutdown: Callable[[], bool] | None = None) -> bool:
        """Attempt deployment with automatic error fixing.

        Args:
            max_attempts: Maximum number of deployment attempts (default: 5).
            check_shutdown: Optional callable that returns True if shutdown is requested.

        Returns:
            bool: True if deployment succeeded.
        """
        # Step 1: Generate deploy.sh and health_check.sh
        if not self._ensure_scripts_exist():
            return False

        # Determine start attempt based on existing logs
        start_attempt = self._get_next_attempt_number()

        end_of_range = start_attempt + max_attempts
        absolute_max_attempts = end_of_range - 1

        # Step 2: Deploy → health-check → repair loop
        #   Each iteration: run deploy.sh, assess health, repair if needed.
        #   Repair modifies scripts; next iteration re-deploys with fixes.
        logger.info("Deploying Application with Error Fixing")
        logger.info(f"Max attempts: {max_attempts} (Starting from #{start_attempt}, up to #{absolute_max_attempts})")

        for attempt in range(start_attempt, end_of_range):
            if check_shutdown and check_shutdown():
                logger.info("Shutdown requested, aborting deployment")
                return False

            emit_progress("deployment", attempt=attempt)
            self.ui.set_stage("Deployment", detail=f"Attempt {attempt}/{absolute_max_attempts}")
            logger.info(f"--- Deployment Attempt #{attempt} ---")

            result = self._run_single_attempt(attempt, max_attempts, absolute_max_attempts, check_shutdown)
            if result is not None:
                return result

        return False

    def _ensure_scripts_exist(self) -> bool:
        """Check for deploy.sh and health_check.sh; generate them if missing.

        Returns True if scripts are available, False on generation failure.
        """
        if self.filesystem.exists(self.deploy_script) and self.filesystem.exists(self.health_check_script):
            logger.success(f"Found existing scripts in {self.sds_dir}")
            return True

        emit_progress("script_generation")
        self.ui.set_stage("Script Generation")
        logger.info("Generating Deployment Scripts")
        logger.info(f"Scripts not found in {self.sds_dir}, generating with {self.agent.__class__.__name__}...")

        success, message = self._script_gen.generate_scripts()

        if success:
            logger.success(message)
            return True

        logger.error(message)
        return False

    def _run_single_attempt(
        self,
        attempt: int,
        max_attempts: int,
        absolute_max_attempts: int,
        check_shutdown: Callable[[], bool] | None,
    ) -> bool | None:
        """Single iteration of the deploy → health-check → repair loop.

        Returns True (success), False (give up), or None (retry next attempt).
        """
        with self.recorder.phase(Phase.DEPLOYMENT, {"attempt": attempt, "max_attempts": max_attempts}) as r:
            # 1. Run deploy.sh
            deploy_result = self._execute_deploy(attempt, r, check_shutdown)
            if check_shutdown and check_shutdown():
                logger.info("Shutdown requested, aborting deployment")
                return False

            # 2. If deploy succeeded, check health
            if deploy_result["success"]:
                verdict = self._assess_health(r)
                if verdict.healthy:
                    return True
            else:
                verdict = None

            # 3. Repair: agent fixes scripts for next attempt
            return self._attempt_repair(
                deploy_result,
                verdict,
                attempt,
                absolute_max_attempts,
                r,
            )

    def _execute_deploy(
        self,
        attempt: int,
        r: TrajectoryRecorderProtocol,
        check_shutdown: Callable[[], bool] | None,
    ) -> CommandResult:
        """Run deploy.sh and record the result in the trajectory."""
        log_file_path = self.sds_dir / "logs" / f"deploy_attempt_{attempt}.log"
        self.filesystem.mkdir(log_file_path.parent, parents=True, exist_ok=True)

        start_time = time.time()
        deploy_result = self.run_deploy_command("start", log_file_path=log_file_path, check_shutdown=check_shutdown)
        deploy_duration = time.time() - start_time

        deploy_ec = deploy_result.get("exit_code")
        r.add_tool_call(
            tool="bash",
            args={"script": ".sds/deploy.sh start"},
            stdout=deploy_result.get("stdout", ""),
            stderr=deploy_result.get("stderr", ""),
            exit_code=int(deploy_ec) if deploy_ec is not None else -1,
            duration=deploy_duration,
        )

        if deploy_result["success"]:
            logger.success("Deployment script succeeded (exit code: 0)")
            r.add_assistant_message(
                "Deployment script executed successfully (exit code: 0)",
                duration=deploy_duration,
            )
        else:
            res = deploy_result["exit_code"]
            logger.error(f"Deployment script failed (exit code: {res})")
            r.add_assistant_message(f"Deployment script failed (exit code: {res}). Analyzing errors...")

        return deploy_result

    def _assess_health(self, r: TrajectoryRecorderProtocol) -> HealthVerdict:
        """Run the health judge and log/record the verdict."""
        judge = AppHealthJudge.from_context(self._ctx, deployment_config=self.deployment_config)
        verdict = judge.assess()

        if verdict.healthy:
            logger.success("Health assessment: healthy")
            logger.success("Deployment Successful!")
            r.add_assistant_message("Health assessment passed. Deployment successful!")
        else:
            logger.warning(f"Health assessment: unhealthy — {verdict.diagnosis}")
            r.add_assistant_message(f"Health assessment: unhealthy. {verdict.assessment}")

        return verdict

    def _attempt_repair(
        self,
        deploy_result: CommandResult,
        health_verdict: HealthVerdict | None,
        attempt: int,
        absolute_max_attempts: int,
        r: TrajectoryRecorderProtocol,
    ) -> bool | None:
        """Ask the repair agent to fix scripts; decide retry vs give up.

        Returns None (retry next attempt) or False (give up).
        """
        log_file_path = self.sds_dir / "logs" / f"deploy_attempt_{attempt}.log"
        if self._fix_with_agent(deploy_result, health_verdict, attempt, absolute_max_attempts, log_file_path):
            r.set_phase_status("needs_retry")
        else:
            if attempt < absolute_max_attempts:
                logger.warning("Agent failed to fix (or crashed), but retrying...")
                r.set_phase_status("needs_retry")
            else:
                r.set_phase_status("failed")
                return False

        return None

    def _fix_with_agent(
        self,
        deploy_result: CommandResult,
        health_verdict: HealthVerdict | None,
        attempt: int,
        max_attempts: int,
        log_file_path: Path | None = None,
        health_check_log_path: Path | None = None,
    ) -> bool:
        """Delegate to RepairAgent for backward compatibility."""
        return self._repair.fix_with_agent(
            deploy_result,
            health_verdict,
            attempt,
            max_attempts,
            log_file_path,
            health_check_log_path,
        )

    def _update_consolidated_summary(self, current_attempt: int, current_summary: str) -> None:
        """Delegate to RepairAgent for backward compatibility."""
        self._repair._update_consolidated_summary(current_attempt, current_summary)

    def run_deploy_command(
        self,
        command: str = "start",
        timeout: int | None = None,
        log_file_path: Path | None = None,
        check_shutdown: Callable[[], bool] | None = None,
    ) -> CommandResult:
        """Run the deployment script with a specific command.

        Delegates to DeployExecutor for backward compatibility.
        """
        return self._executor.run_deploy_command(
            command=command,
            timeout=timeout,
            log_file_path=log_file_path,
            check_shutdown=check_shutdown,
        )
