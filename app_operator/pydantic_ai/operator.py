"""PydanticAIOperator — procedural lifecycle using Pydantic AI agents."""

import signal
import threading
import time
from pathlib import Path

from app_operator.config import Config, load_config
from app_operator.constants import DEPLOYMENT_PROGRESS_FILENAME
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.guardrails import ArtifactGuardrail
from app_operator.logger import logger
from app_operator.operator_base import OperatorBase
from app_operator.progress import emit_progress
from app_operator.prompts import (
    PromptLoader,
    analyze_repository,
    create_fix_prompt,
    create_generate_script_prompt,
    prepare_error_context,
)
from app_operator.pydantic_ai._agents import (
    build_analyze_agent,
    build_fix_agent,
    build_health_agent,
    build_script_agent,
)
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.pydantic_ai._models import build_model_str
from app_operator.pydantic_ai._responses import HealthVerdictResponse
from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder
from app_operator.pydantic_ai.tools import build_tools
from app_operator.script_runner import run_script, write_log_file
from app_operator.trajectory import Phase
from app_operator.types import HealthVerdict


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

        if not self.config.agent.provider:
            raise ValueError("agent.provider must be set for pydantic_ai runtime")
        if not self.config.agent.model:
            raise ValueError("agent.model must be set for pydantic_ai runtime")

        self.sds_dir = self.repo_path / ".sds"
        self._persist_deployment_config()

        # Build model string
        self.model_str = build_model_str(self.config)

        # Build tools
        self.tool_list = build_tools(self.config)

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

        # Build agents
        self.analyze_agent = build_analyze_agent(self.model_str, self.tool_list)
        self.script_agent = build_script_agent(self.model_str, self.tool_list)
        self.fix_agent = build_fix_agent(self.model_str, self.tool_list)
        self.health_agent = build_health_agent(self.model_str, self.tool_list)

        # Trajectory recorder (native pydantic-ai format)
        self.recorder = PydanticAITrajectoryRecorder(self.repo_path)
        self.recorder.set_agent_name("PydanticAI")

        self._deployed = False
        self._token_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "requests": 0}

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
            logger.info(f"Total Token Usage: {self._token_usage}")
            emit_progress("finishing")
            return 0

        except KeyboardInterrupt:
            logger.info("Shutting down due to interrupt...")
            _status = "interrupted"
            return 1

        except (OSError, RuntimeError, ValueError) as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return 1
        finally:
            self.recorder.record_token_usage(self._token_usage)
            self.recorder.finalize(_status)

    def _accumulate_usage(self, result) -> None:
        """Accumulate token usage from a RunResult."""
        usage = result.usage()
        self._token_usage["input_tokens"] += usage.input_tokens or 0
        self._token_usage["output_tokens"] += usage.output_tokens or 0
        self._token_usage["requests"] += usage.requests or 0

    def _run_analysis(self) -> None:
        """Run code analysis phase."""
        sds_dir = self.repo_path / ".sds"
        analysis_file = sds_dir / "code_analysis.md"
        issues_file = sds_dir / "deployment_issues.md"

        if self.filesystem.exists(analysis_file) and self.filesystem.exists(issues_file):
            logger.info("Code analysis files already exist. Skipping analysis.")
            return

        emit_progress("code_analysis")
        system_prompt = self.deps.loader.render("code_analyzer/system.jinja2")
        user_prompt = self.deps.loader.render("code_analyzer/user.jinja2", repo_path=self.repo_path)

        result = self.analyze_agent.run_sync(
            user_prompt,
            deps=self.deps,
            instructions=system_prompt,
        )
        self._accumulate_usage(result)
        self.recorder.record_run(Phase.EXPLORATION, "Code Analyzer", result)

        # Guardrail retries
        guardrail = ArtifactGuardrail([".sds/code_analysis.md", ".sds/deployment_issues.md"])
        for retry in range(guardrail.max_retries):
            missing = guardrail.missing(self.repo_path, self.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: missing {} (retry {}/{})", missing, retry + 1, guardrail.max_retries)
            result = self.analyze_agent.run_sync(
                guardrail.reminder(missing),
                deps=self.deps,
                message_history=result.all_messages(),
            )
            self._accumulate_usage(result)
            self.recorder.record_run(Phase.EXPLORATION, "Code Analyzer (retry)", result)
        else:
            missing = guardrail.missing(self.repo_path, self.filesystem)
            if missing:
                logger.warning("Guardrail: artifacts still missing after max retries: {}", missing)

    def _generate_scripts(self) -> None:
        """Generate deploy.sh and health_check.sh."""
        emit_progress("script_generation")
        system_prompt = self.deps.loader.render(
            "script_generator/system.jinja2",
            platform=self.config.deployment.platform,
        )
        repo_context = analyze_repository(self.repo_path)

        deploy_prompt = create_generate_script_prompt(
            script_name="deploy.sh",
            repo_context=repo_context,
            target_dir=str(self.repo_path),
            platform=self.config.deployment.platform,
        )
        health_prompt = create_generate_script_prompt(
            script_name="health_check.sh",
            repo_context=repo_context,
            target_dir=str(self.repo_path),
            platform=self.config.deployment.platform,
        )

        # Generate deploy.sh
        deploy_guardrail = ArtifactGuardrail([".sds/deploy.sh"])
        result = self.script_agent.run_sync(
            deploy_prompt,
            deps=self.deps,
            instructions=system_prompt,
        )
        self._accumulate_usage(result)
        self.recorder.record_run(Phase.SCRIPT_GENERATION, "Script Generator", result)

        for retry in range(deploy_guardrail.max_retries):
            missing = deploy_guardrail.missing(self.repo_path, self.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: {} missing (retry {}/{})", missing, retry + 1, deploy_guardrail.max_retries)
            result = self.script_agent.run_sync(
                deploy_guardrail.reminder(missing),
                deps=self.deps,
                message_history=result.all_messages(),
            )
            self._accumulate_usage(result)
            self.recorder.record_run(Phase.SCRIPT_GENERATION, "Script Generator (retry)", result)

        # Generate health_check.sh
        health_guardrail = ArtifactGuardrail([".sds/health_check.sh"])
        result = self.script_agent.run_sync(
            health_prompt,
            deps=self.deps,
            instructions=system_prompt,
        )
        self._accumulate_usage(result)
        self.recorder.record_run(Phase.SCRIPT_GENERATION, "Script Generator", result)

        for retry in range(health_guardrail.max_retries):
            missing = health_guardrail.missing(self.repo_path, self.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: {} missing (retry {}/{})", missing, retry + 1, health_guardrail.max_retries)
            result = self.script_agent.run_sync(
                health_guardrail.reminder(missing),
                deps=self.deps,
                message_history=result.all_messages(),
            )
            self._accumulate_usage(result)
            self.recorder.record_run(Phase.SCRIPT_GENERATION, "Script Generator (retry)", result)

    def _deploy_with_retries(self) -> bool:
        """Deploy and fix in a loop. Returns True if healthy."""
        for attempt in range(1, self.max_deployment_attempts + 1):
            if self._shutdown_requested:
                return False

            emit_progress("deployment", attempt=attempt)
            logger.info(f"Deployment attempt {attempt}/{self.max_deployment_attempts}")

            # Run deploy script
            log_file = self.repo_path / ".sds" / "logs" / f"deploy_attempt_{attempt}.log"
            deploy_result = run_script(
                self.repo_path,
                self.filesystem,
                ".sds/deploy.sh start",
                log_file_path=log_file,
                timeout=self.config.operator.deploy_timeout,
            )

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

        health_check_script = self.repo_path / ".sds" / "health_check.sh"
        platform = self.config.deployment.platform

        deployment_progress_path = None
        if self.config.operator.phase.fix_summary_consolidation:
            deployment_progress_path = self.repo_path / ".sds" / DEPLOYMENT_PROGRESS_FILENAME
        has_deployment_progress = deployment_progress_path is not None and deployment_progress_path.exists()

        system_prompt = self.deps.loader.render("health_judge_agent/system.jinja2")
        user_prompt = self.deps.loader.render(
            "health_judge_agent/user.jinja2",
            repo_path=self.repo_path,
            health_check_script=health_check_script,
            platform=platform,
            structured_output=True,
            deployment_progress_path=deployment_progress_path,
            has_deployment_progress=has_deployment_progress,
        )

        result = self.health_agent.run_sync(
            user_prompt,
            deps=self.deps,
            instructions=system_prompt,
        )
        self._accumulate_usage(result)
        self.recorder.record_run(Phase.DEPLOYMENT, "Health Judge", result, context={"attempt": attempt})

        verdict = result.output

        # Save assessment log
        log_file = self.repo_path / ".sds" / "logs" / f"health_check_attempt_{attempt}.log"
        status = "healthy" if verdict.healthy else "unhealthy"
        content = (
            f"=== Health Assessment ===\n"
            f"Status: {status}\n"
            f"Script fixed: {verdict.script_was_fixed}\n\n"
            f"Assessment: {verdict.assessment}\n"
        )
        if verdict.diagnosis:
            content += f"\nDiagnosis: {verdict.diagnosis}\n"
        write_log_file(self.filesystem, log_file, content)

        return verdict

    def _fix_errors(self, deploy_result: dict, health_verdict: HealthVerdictResponse | None, attempt: int) -> None:
        """Run fix agent to diagnose and repair issues."""
        log_file_path = self.repo_path / ".sds" / "logs" / f"deploy_attempt_{attempt}.log"
        health_check_log_path = None
        hv = None
        if health_verdict is not None:
            health_check_log_path = self.repo_path / ".sds" / "logs" / f"health_check_attempt_{attempt}.log"
            hv = HealthVerdict(
                healthy=health_verdict.healthy,
                assessment=health_verdict.assessment,
                diagnosis=health_verdict.diagnosis,
                script_was_fixed=health_verdict.script_was_fixed,
                raw_response="",
            )

        error_context = prepare_error_context(deploy_result, hv, log_file_path, health_check_log_path)

        platform = self.config.deployment.platform
        deployment_progress_path = None
        if self.config.operator.phase.fix_summary_consolidation:
            deployment_progress_path = self.repo_path / ".sds" / DEPLOYMENT_PROGRESS_FILENAME

        system_prompt = self.deps.loader.render("repair_agent/system.jinja2")
        prompt = create_fix_prompt(
            repo_path=self.repo_path,
            attempt=attempt,
            max_attempts=self.max_deployment_attempts,
            error_context=error_context,
            deploy_script_path=self.repo_path / ".sds" / "deploy.sh",
            health_check_script_path=self.repo_path / ".sds" / "health_check.sh",
            platform=platform,
            deployment_progress_path=deployment_progress_path,
            structured_output=True,
        )

        result = self.fix_agent.run_sync(
            prompt,
            deps=self.deps,
            instructions=system_prompt,
        )
        self._accumulate_usage(result)
        self.recorder.record_run(Phase.DEPLOYMENT, "Error Fixer", result, context={"attempt": attempt})

        summary_text = result.output.summary.strip() if result.output else None
        if summary_text:
            log_file = self.repo_path / ".sds" / "logs" / f"fix_summary_{attempt}.log"
            write_log_file(self.filesystem, log_file, summary_text)

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

            health_check_script = self.repo_path / ".sds" / "health_check.sh"
            platform = self.config.deployment.platform

            deployment_progress_path = None
            if self.config.operator.phase.fix_summary_consolidation:
                deployment_progress_path = self.repo_path / ".sds" / DEPLOYMENT_PROGRESS_FILENAME
            has_deployment_progress = deployment_progress_path is not None and deployment_progress_path.exists()

            system_prompt = self.deps.loader.render("health_judge_agent/system.jinja2")
            user_prompt = self.deps.loader.render(
                "health_judge_agent/user.jinja2",
                repo_path=self.repo_path,
                health_check_script=health_check_script,
                platform=platform,
                structured_output=True,
                deployment_progress_path=deployment_progress_path,
                has_deployment_progress=has_deployment_progress,
            )

            result = self.health_agent.run_sync(
                user_prompt,
                deps=self.deps,
                instructions=system_prompt,
            )
            self._accumulate_usage(result)
            self.recorder.record_run(Phase.MONITORING, "Health Judge", result, context={"cycle": cycle})

            log_file = (
                self.repo_path / ".sds" / "logs" / "monitor" / f"check_{cycle}_{time.strftime('%Y%m%d-%H%M%S')}.log"
            )
            verdict = result.output
            status = "healthy" if verdict.healthy else "unhealthy"
            content = (
                f"=== Health Assessment ===\n"
                f"Status: {status}\n"
                f"Script fixed: {verdict.script_was_fixed}\n\n"
                f"Assessment: {verdict.assessment}\n"
            )
            if verdict.diagnosis:
                content += f"\nDiagnosis: {verdict.diagnosis}\n"
            write_log_file(self.filesystem, log_file, content)

    def _handle_shutdown_signal(self, signum: int, frame) -> None:
        if not self._shutdown_requested:
            self._shutdown_requested = True
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            logger.info(f"Received {signal_name} signal. Initiating graceful shutdown...")
            if signum == signal.SIGINT:
                raise KeyboardInterrupt
