from __future__ import annotations

import subprocess  # noqa: F401 - re-exported for legacy monkeypatch paths
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
from app_operator.healthcheck import append_validation_verdict, run_health_check
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.prompts import get_loader
from app_operator.repo_evidence import RepoEvidence, extract_repo_evidence
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    Phase,
    TrajectoryRecorderProtocol,
)
from app_operator.ui_protocol import NullOperatorUI, OperatorUI

FIX_SUMMARY_CONSOLIDATION_INTERVAL = 1


class DeploymentAgent:
    """Orchestrates the deploy-check-fix loop by composing focused agents."""

    # Grace-period rechecks after an initial unhealthy verdict, before the fix
    # agent is invoked.  Allows services that are still starting up to become
    # healthy without an expensive redeploy.  Override in tests to skip waits.
    _HEALTH_GRACE_RECHECKS: int = 2
    _HEALTH_GRACE_SLEEP: int = 15  # seconds between grace rechecks
    _HEALTH_SUCCESS_CONFIRMATION_RECHECKS: int = 1
    _HEALTH_SUCCESS_CONFIRMATION_SLEEP: int = 0

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
        self._last_preflight_errors: str | None = None
        self._repo_evidence: RepoEvidence | None = None

        # Composed agents
        self._executor = DeployExecutor(self._ctx)
        self._repair = RepairAgent(self._ctx, deployment_config=self.deployment_config)
        self._script_gen = ScriptGeneratorAgent(self._ctx, deployment_config=self.deployment_config)

    def _get_repo_evidence(self) -> RepoEvidence:
        """Return cached repository evidence, extracting it on first call."""
        if self._repo_evidence is None:
            self._repo_evidence = extract_repo_evidence(self.repo_path)
        return self._repo_evidence

    def _create_health_judge(self, recorder: TrajectoryRecorderProtocol) -> AppHealthJudge:
        """Create a health judge bound to the current deployment attempt."""
        return AppHealthJudge(
            repo_path=self.repo_path,
            coding_agent=self.agent,
            health_check_script=self.health_check_script,
            filesystem=self.filesystem,
            operator_config=self.operator_config,
            recorder=recorder,
            dspy_config=self.dspy_config,
            ui=self.ui,
            deployment_config=self.deployment_config,
        )

    def _run_health_check_and_assess(
        self,
        *,
        recorder: TrajectoryRecorderProtocol,
        log_file_path: Path,
        script_label: str,
    ) -> tuple[CommandResult, HealthVerdict]:
        """Run health_check.sh, record the tool call, and normalize the verdict."""
        health_start = time.time()
        health_result = run_health_check(
            self.repo_path,
            self.health_check_script,
            log_file_path=log_file_path,
        )
        health_duration = time.time() - health_start

        health_ec = health_result.get("exit_code")
        recorder.add_tool_call(
            tool="bash",
            args={"script": script_label},
            stdout=health_result.get("stdout", ""),
            stderr=health_result.get("stderr", ""),
            exit_code=int(health_ec) if health_ec is not None else -1,
            duration=health_duration,
        )

        health_verdict = self._create_health_judge(recorder).assess()
        normalized_verdict = self._normalize_health_verdict(health_result, health_verdict)
        append_validation_verdict(log_file_path, is_healthy=normalized_verdict.healthy)
        return health_result, normalized_verdict

    @staticmethod
    def _normalize_health_verdict(
        health_result: CommandResult,
        health_verdict: HealthVerdict,
    ) -> HealthVerdict:
        """Refuse healthy verdicts that contradict the health check exit status."""
        if not health_verdict.healthy:
            return health_verdict

        exit_code = int(health_result.get("exit_code", -1))
        if exit_code == 0:
            return health_verdict

        reason = f"Health judge returned healthy but health_check.sh exited non-zero (exit_code={exit_code})."
        return HealthVerdict(
            healthy=False,
            assessment=reason,
            diagnosis=reason,
            script_was_fixed=health_verdict.script_was_fixed,
            raw_response=health_verdict.raw_response,
        )

    def _confirm_healthy_deployment(
        self,
        *,
        attempt: int,
        recorder: TrajectoryRecorderProtocol,
    ) -> tuple[bool, CommandResult | None, HealthVerdict | None]:
        """Require consecutive healthy confirmation checks before early success."""
        if self._HEALTH_SUCCESS_CONFIRMATION_RECHECKS <= 0:
            return True, None, None

        for confirm_idx in range(1, self._HEALTH_SUCCESS_CONFIRMATION_RECHECKS + 1):
            if self._HEALTH_SUCCESS_CONFIRMATION_SLEEP > 0:
                time.sleep(self._HEALTH_SUCCESS_CONFIRMATION_SLEEP)

            confirm_log = self.sds_dir / "logs" / f"health_confirm_attempt_{attempt}_{confirm_idx}.log"
            health_result, health_verdict = self._run_health_check_and_assess(
                recorder=recorder,
                log_file_path=confirm_log,
                script_label=(
                    ".sds/health_check.sh "
                    f"(success confirmation {confirm_idx}/{self._HEALTH_SUCCESS_CONFIRMATION_RECHECKS})"
                ),
            )
            if not health_verdict.healthy:
                recorder.add_assistant_message(
                    "Healthy verdict was not stable enough to trust. "
                    f"Success confirmation {confirm_idx}/{self._HEALTH_SUCCESS_CONFIRMATION_RECHECKS} "
                    f"failed: {health_verdict.diagnosis or health_verdict.assessment}"
                )
                return False, health_result, health_verdict

        recorder.add_assistant_message("Health confirmation rechecks passed. Deployment is considered healthy.")
        return True, None, None

    def _get_next_attempt_number(self) -> int:
        """Determine the next attempt number based on existing logs."""
        logs_dir = self.sds_dir / "logs"
        if not self.filesystem.exists(logs_dir):
            return 1

        # Find all deploy logs using filesystem abstraction
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

    def _detect_fix_loop(self, attempt: int) -> str | None:
        """Detect if the agent is stuck applying the same fix repeatedly.

        Reads fix_summary_N.log files for the last few attempts and compares
        their content for similarity. Returns a warning string if a loop is
        detected, or None if no loop is found.
        """
        if attempt < 3:
            return None

        logs_dir = self.sds_dir / "logs"
        # Read the last 3 fix summaries (attempts attempt-3 through attempt-1)
        summaries: list[str] = []
        for i in range(attempt - 3, attempt):
            log_file = logs_dir / f"fix_summary_{i}.log"
            try:
                if self.filesystem.exists(log_file):
                    summaries.append(self.filesystem.read_text(log_file).strip())
            except (OSError, AttributeError):
                pass

        if not summaries:
            return None

        def _similar(a: str, b: str) -> bool:
            """Return True if two summaries share enough common tokens."""
            tokens_a = set(a.lower().split())
            tokens_b = set(b.lower().split())
            if not tokens_a or not tokens_b:
                return False
            overlap = len(tokens_a & tokens_b)
            shorter = min(len(tokens_a), len(tokens_b))
            return (overlap / shorter) >= 0.7

        # Check if ALL consecutive recent summaries are similar (3-loop)
        if len(summaries) >= 3:
            if _similar(summaries[-1], summaries[-2]) and _similar(summaries[-2], summaries[-3]):
                n = len(summaries)
                return (
                    f"\n\n## CRITICAL: Fix Loop Detected\n"
                    f"The last {n} consecutive repair attempts applied nearly identical fixes. "
                    "You appear to be stuck in a loop.\n\n"
                    "**You MUST take a fundamentally different approach this time.**\n\n"
                    "Suggestions to break out of the loop:\n"
                    "- Read the container logs (`docker compose logs`) to understand the root cause\n"
                    "- Question your assumptions about which service or config is the real problem\n"
                    "- Try a completely different fix strategy\n"
                    "- Look for indirect causes (networking, permissions, dependency order)"
                )

        # Check if only the last 2 are similar (2-loop)
        if len(summaries) >= 2 and _similar(summaries[-1], summaries[-2]):
            return (
                "\n\n## WARNING: Possible Fix Loop\n"
                "The last 2 attempts applied similar fixes without success.\n\n"
                "**You MUST take a fundamentally different approach this time.**\n\n"
                "Suggestions to break out of the loop:\n"
                "- Read the container logs (`docker compose logs`) to understand the root cause\n"
                "- Question your assumptions about which service or config is the real problem\n"
                "- Try a completely different fix strategy\n"
                "- Look for indirect causes (networking, permissions, dependency order)"
            )

        return None

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
        self._prepare_fix_summary_for_run(start_attempt)

        # max_attempts is the absolute ceiling — total attempts ever, not additional ones.
        absolute_max_attempts = max_attempts
        end_of_range = absolute_max_attempts + 1

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

    def _prepare_fix_summary_for_run(self, start_attempt: int) -> None:
        """Remove stale consolidated summary when starting from attempt 1."""
        summary_file = self.sds_dir / "fix_summary.md"
        if start_attempt == 1 and self.filesystem.exists(summary_file):
            self.filesystem.remove(summary_file)

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

    def _update_consolidated_summary(self, attempt: int, summary: str) -> None:
        """Update the legacy consolidated fix summary file.

        The newer deployment-progress flow records hypotheses in
        ``deployment_progress.md``, but some tests and callers still exercise
        this older summary hook.
        """
        if attempt % FIX_SUMMARY_CONSOLIDATION_INTERVAL != 0:
            return

        summary_file = self.sds_dir / "fix_summary.md"
        existing_summary = self.filesystem.read_text(summary_file) if self.filesystem.exists(summary_file) else ""
        new_attempts_text = f"## Attempt {attempt}\n{summary}"
        prompt = get_loader().render(
            "deployer/consolidate_summary.jinja2",
            existing_summary=existing_summary,
            new_attempts_text=new_attempts_text,
        )
        updated = self.agent.generate(
            prompt,
            cwd=str(self.repo_path),
            timeout=self.operator_config.agent_fix_timeout,
        )
        self.filesystem.write_text(summary_file, updated)

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
