"""PydanticAIOperator — procedural lifecycle using Pydantic AI agents."""

import signal
import threading
import time
from pathlib import Path

from app_operator.config import Config, load_config
from app_operator.exceptions import ProcessError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.operator_base import OperatorBase
from app_operator.progress import emit_progress
from app_operator.prompts import PromptLoader
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.pydantic_ai._models import build_model_settings, build_model_str
from app_operator.pydantic_ai._responses import HealthVerdictResponse
from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder
from app_operator.pydantic_ai.agents.analyze import AnalyzeAgent
from app_operator.pydantic_ai.agents.health import HealthAgent
from app_operator.pydantic_ai.agents.repair import RepairAgent
from app_operator.pydantic_ai.agents.script import ScriptAgent
from app_operator.pydantic_ai.tools import build_tools
from app_operator.script_runner import run_script
from app_operator.trajectory import Phase
from app_operator.types import CommandResult


class PydanticAIOperator(OperatorBase):
    """Pydantic AI-based operator for deployment and monitoring."""

    def __init__(
        self,
        repo_path: str,
        health_check_interval: int = 30,
        health_check_max_count: int | None = 5,
        max_deployment_attempts: int = 5,
        filesystem: FileSystemInterface | None = None,
        config: Config | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.health_check_interval = health_check_interval
        self.health_check_max_count = health_check_max_count
        self.max_deployment_attempts = max_deployment_attempts
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()

        if not self.filesystem.exists(self.repo_path):
            raise ValueError(f"Repository path does not exist: {repo_path}")
        if not self.filesystem.is_dir(self.repo_path):
            raise ValueError(f"Repository path is not a directory: {repo_path}")

        if config is None:
            self.config = load_config(str(self.repo_path))
        else:
            self.config = config

        if not self.config.agent.backend:
            raise ValueError("agent.backend must be set for pydantic_ai runtime")
        if not self.config.agent.model:
            raise ValueError("agent.model must be set for pydantic_ai runtime")

        self.sds_dir = self.repo_path / ".sds"
        self._persist_deployment_config()

        # Build model string and settings
        self.model_str = build_model_str(self.config)
        self.model_settings = build_model_settings(self.config)

        # Build tools
        self.tool_list = build_tools()

        # Build deps
        self._shutdown_requested = False
        loader = PromptLoader(dspy_config=self.config.dspy)
        self.deps = OperatorDeps(
            repo_path=self.repo_path,
            filesystem=self.filesystem,
            loader=loader,
            config=self.config,
            check_shutdown=lambda: self._shutdown_requested,
        )

        # Trajectory recorder (native pydantic-ai format)
        self.recorder = PydanticAITrajectoryRecorder(self.repo_path)

        # Build agents
        self.analyze_agent = AnalyzeAgent(self.model_str, self.model_settings, self.tool_list, self.deps, self.recorder)
        self.script_agent = ScriptAgent(self.model_str, self.model_settings, self.tool_list, self.deps, self.recorder)
        self.repair_agent = RepairAgent(
            self.model_str,
            self.model_settings,
            self.tool_list,
            self.deps,
            self.recorder,
            max_attempts=self.max_deployment_attempts,
        )
        self.health_agent = HealthAgent(self.model_str, self.model_settings, self.tool_list, self.deps, self.recorder)

        self._deployed = False

    def run(self) -> int:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._handle_shutdown_signal)
            signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

        _status = "failed"
        try:
            logger.info("Starting Pydantic AI App Operator Mode")
            logger.info(f"Repository: {self.repo_path}")
            logger.info(f"Model: {self.model_str}")
            logger.info(f"Deployment Platform: {self.config.deployment.platform}")
            logger.info(f"Deployment Target: {self.config.deployment.target}")
            if self.config.agent.thinking_budget:
                logger.info(f"Thinking Budget: {self.config.agent.thinking_budget} tokens")

            # Phase 1: Code Analysis
            if self.config.operator.phase.code_analysis:
                self._run_analysis()
            else:
                logger.info("Code analysis disabled by configuration, skipping")

            if self._shutdown_requested:
                _status = "interrupted"
                return 1

            # Phase 2: Script Generation
            self._generate_scripts()

            if self._shutdown_requested:
                _status = "interrupted"
                return 1

            # Phase 3: Deploy with retries
            self._deployed = self._deploy_with_retries()

            # Phase 4: Monitoring
            if self._deployed and self.config.operator.phase.health_monitoring:
                self._monitor()
            elif not self._deployed:
                logger.error("Deployment failed after max attempts.")

            _status = "completed" if self._deployed else "failed"
            logger.info(f"Total Token Usage: {self.recorder.total_usage}")
            emit_progress("finishing")
            return 0

        except KeyboardInterrupt:
            logger.info("Received interrupt signal. Shutting down gracefully...")
            _status = "interrupted"
            return 1

        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return 1
        finally:
            self.recorder.finalize(_status)

    def _run_analysis(self) -> None:
        """Run code analysis phase."""
        self.analyze_agent.run()

    def _generate_scripts(self) -> None:
        """Generate deploy.sh and health_check.sh."""
        self.script_agent.run()

    def _deploy_with_retries(self) -> bool:
        """Deploy and fix in a loop. Returns True if healthy."""
        for attempt in range(1, self.max_deployment_attempts + 1):
            if self._shutdown_requested:
                return False

            emit_progress("deployment", attempt=attempt)
            logger.info(f"Deployment attempt {attempt}/{self.max_deployment_attempts}")

            # Run deploy script
            log_file = self.repo_path / ".sds" / "logs" / f"deploy_attempt_{attempt}.log"
            try:
                deploy_result = run_script(
                    self.repo_path,
                    self.filesystem,
                    ".sds/deploy.sh start",
                    log_file_path=log_file,
                    timeout=self.config.operator.deploy_timeout,
                )
            except ProcessError as e:
                logger.error(f"Process error during deployment: {e}")
                deploy_result: CommandResult = {
                    "success": False,
                    "exit_code": e.exit_code if e.exit_code is not None else -1,
                    "stdout": "",
                    "stderr": str(e),
                }

            # Run health check
            verdict = self._run_health_check(attempt)
            if verdict is not None and verdict.healthy:
                logger.info("Application is healthy!")
                return True

            # Fix errors if not last attempt
            if attempt < self.max_deployment_attempts:
                self._fix_errors(deploy_result, verdict, attempt)

        return False

    def _run_health_check(self, attempt: int) -> HealthVerdictResponse | None:
        """Run agent-based health assessment."""
        logger.info("Running agent-based health assessment...")
        return self.health_agent.run_check(phase=Phase.DEPLOYMENT, attempt=attempt)

    def _fix_errors(
        self, deploy_result: CommandResult, health_verdict: HealthVerdictResponse | None, attempt: int
    ) -> None:
        """Run fix agent to diagnose and repair issues."""
        self.repair_agent.run(deploy_result, health_verdict, attempt)

    def _monitor(self) -> None:
        """Periodic health monitoring."""
        if not self.health_check_max_count:
            return

        logger.info(f"Starting monitoring (max {self.health_check_max_count} checks)...")

        for cycle in range(1, self.health_check_max_count + 1):
            if self._shutdown_requested:
                break

            if cycle > 1 and self.health_check_interval > 0:
                time.sleep(self.health_check_interval)

            emit_progress("monitoring", cycle=cycle)
            logger.info(f"Running health assessment (monitor cycle {cycle})...")

            self.health_agent.run_check(phase=Phase.MONITORING, cycle=cycle)

    def _handle_shutdown_signal(self, signum: int, frame) -> None:
        self._shutdown_requested = True
        if signum == signal.SIGINT:
            # Restore default handler so a second Ctrl-C force-quits immediately.
            # Avoid calling logger here — logging locks can cause a deadlock when
            # the signal interrupts a log call on the main thread.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            raise KeyboardInterrupt
