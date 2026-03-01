import re
import subprocess
import time
from pathlib import Path
from typing import Optional, Callable, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app_operator.dspy_integration.config import DSPyConfig

from app_operator.ui import OperatorUI, NullOperatorUI
from libs.agent_cli.base import CodingAgent
from libs.agent_cli.factory import create_agent_from_config
from app_operator.config import DeploymentConfig, OperatorConfig
from app_operator.exceptions import AgentError, DeploymentError, FileSystemError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.prompts.deployment_context import (
    analyze_repository,
    create_system_prompt,
)
from app_operator.prompts.deployer import (
    create_generate_script_prompt,
    create_fix_prompt,
    prepare_error_context,
)
from app_operator.cli_agent.subprocess_runner import SubprocessRunner
from app_operator.cli_agent.progress_summarizer import ProgressSummarizer
from app_operator.cli_agent.healthcheck import run_health_check
from app_operator.trajectory import (
    Phase,
    TrajectoryRecorderProtocol,
    NullTrajectoryRecorder,
)


FIX_SUMMARY_CONSOLIDATION_INTERVAL = 1
FIX_SUMMARY_FILENAME = "fix_summary.md"


def get_fix_summary_path(sds_dir: Path) -> Path:
    """Return the path to the consolidated fix summary file."""
    return sds_dir / FIX_SUMMARY_FILENAME


def generate_scripts(
    target_dir: str,
    agent: Optional[CodingAgent] = None,
    filesystem: Optional[FileSystemInterface] = None,
    deployment_config: Optional[DeploymentConfig] = None,
    operator_config: Optional[OperatorConfig] = None,
    recorder: Optional[TrajectoryRecorderProtocol] = None,
    dspy_config: Optional["DSPyConfig"] = None,
) -> tuple[bool, str]:
    """Generate deploy.sh and health_check.sh scripts using a coding agent.

    This function runs the coding agent in the target directory with read/write access,
    analyzing the repository structure and generating appropriate deployment
    and health check scripts.

    Args:
        target_dir: The directory path where scripts should be generated.
        agent: Optional CodingAgent instance. If None, creates one from config.
        filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
        deployment_config: Optional deployment configuration. If None, uses default.
        operator_config: Optional operator configuration for timeouts. If None, uses default.
        recorder: Optional trajectory recorder.
        dspy_config: Optional DSPy configuration for optimized prompts.

    Returns:
        Tuple of (success: bool, message: str).
    """
    if filesystem is None:
        filesystem = RealFilesystem()

    if deployment_config is None:
        deployment_config = DeploymentConfig()

    if operator_config is None:
        operator_config = OperatorConfig()

    recorder = recorder or NullTrajectoryRecorder()

    target_path = Path(target_dir).resolve()

    # Validate target directory exists
    if not filesystem.exists(target_path):
        return False, f"Target directory does not exist: {target_dir}"

    if not filesystem.is_dir(target_path):
        return False, f"Target path is not a directory: {target_dir}"

    # Initialize coding agent if not provided
    if agent is None:
        try:
            agent = create_agent_from_config(str(target_path))
        except (RuntimeError, AgentError) as e:
            return False, str(e)

    agent.recorder = recorder

    # Create .sds directory if it doesn't exist
    sds_dir = target_path / ".sds"
    filesystem.mkdir(sds_dir, exist_ok=True)

    # Get absolute path for context
    abs_target_dir = str(target_path)

    # Start script generation phase in trajectory
    with recorder.phase(Phase.SCRIPT_GENERATION) as r:
        try:
            # Create system prompt based on deployment platform
            system_prompt = create_system_prompt(deployment_config.platform)

            # Analyze the repository structure
            repo_context = analyze_repository(target_path)

            # Generate deploy.sh using coding agent
            deploy_success, deploy_msg = _generate_script(
                agent,
                system_prompt,
                repo_context,
                abs_target_dir,
                filesystem,
                "deploy.sh",
                deployment_config,
                operator_config,
                recorder=r,
                dspy_config=dspy_config,
            )

            if not deploy_success:
                r.set_phase_status("failed")
                return False, f"Failed to generate deploy.sh: {deploy_msg}"

            # Generate health_check.sh using coding agent
            health_check_success, health_check_msg = _generate_script(
                agent,
                system_prompt,
                repo_context,
                abs_target_dir,
                filesystem,
                "health_check.sh",
                deployment_config,
                operator_config,
                recorder=r,
                dspy_config=dspy_config,
            )

            # Make scripts executable
            deploy_script_path = sds_dir / "deploy.sh"
            health_check_script_path = sds_dir / "health_check.sh"

            if filesystem.exists(deploy_script_path):
                filesystem.chmod(deploy_script_path, 0o755)

            if filesystem.exists(health_check_script_path):
                filesystem.chmod(health_check_script_path, 0o755)

            return True, f"Successfully generated scripts in {sds_dir}"

        except (AgentError, DeploymentError, FileSystemError) as e:
            r.set_phase_status("failed")
            return False, f"Failed to generate scripts: {e}"
        except Exception as e:
            # Catch any unexpected errors and log them
            r.set_phase_status("failed")
            return False, f"Unexpected error during script generation: {e}"


def _generate_script(
    agent: CodingAgent,
    system_prompt: str,
    repo_context: str,
    target_dir: str,
    filesystem: FileSystemInterface,
    script_name: str,
    deployment_config: Optional[DeploymentConfig] = None,
    operator_config: Optional[OperatorConfig] = None,
    recorder: Optional[TrajectoryRecorderProtocol] = None,
    dspy_config: Optional["DSPyConfig"] = None,
) -> tuple[bool, str]:
    """Generate a script (deploy.sh or health_check.sh) using a coding agent.

    Args:
        agent: The coding agent to use for generation.
        system_prompt: System prompt for the agent.
        repo_context: Repository analysis context.
        target_dir: Target directory path.
        filesystem: Filesystem abstraction.
        script_name: Name of the script to generate (e.g. "deploy.sh").
        deployment_config: Optional deployment configuration.
        operator_config: Optional operator configuration for timeouts.
        recorder: Optional trajectory recorder.
        dspy_config: Optional DSPy configuration for optimized prompts.

    Returns:
        Tuple of (success: bool, message: str).
    """
    if operator_config is None:
        operator_config = OperatorConfig()

    recorder = recorder or NullTrajectoryRecorder()

    platform = deployment_config.platform if deployment_config else "auto"
    full_prompt = create_generate_script_prompt(
        system_prompt=system_prompt,
        script_name=script_name,
        repo_context=repo_context,
        target_dir=target_dir,
        platform=platform,
        dspy_config=dspy_config,
        recorder=recorder,
    )

    try:
        start_time = time.time()
        agent.generate(
            full_prompt, cwd=target_dir, timeout=operator_config.agent_timeout
        )

        duration = time.time() - start_time
        logger.info(f"Agent generation took {duration / 60:.2f} minutes")

        script_path = Path(target_dir) / ".sds" / script_name
        if filesystem.exists(script_path):
            return True, f"Successfully generated {script_name}"
        else:
            return False, f"Agent failed to create .sds/{script_name}"

    except subprocess.TimeoutExpired:
        timeout = operator_config.agent_timeout // 60
        recorder.add_assistant_message(
            f"Script generation timed out after {timeout} minutes"
        )
        return (
            False,
            f"agent command timed out after {timeout} minutes",
        )
    except AgentError as e:
        recorder.add_assistant_message(f"Script generation failed: {e}")
        return False, str(e)
    except Exception as e:
        recorder.add_assistant_message(f"Script generation failed: {e}")
        return False, str(e)


class DeploymentAgent:
    """Agent responsible for deploying applications and fixing deployment errors."""

    def __init__(
        self,
        repo_path: Path,
        coding_agent: CodingAgent,
        filesystem: Optional[FileSystemInterface] = None,
        deployment_config: Optional[DeploymentConfig] = None,
        operator_config: Optional[OperatorConfig] = None,
        recorder: Optional[TrajectoryRecorderProtocol] = None,
        dspy_config: Optional["DSPyConfig"] = None,
        ui: Optional[OperatorUI] = None,
    ):
        """Initialize the deployment agent.

        Args:
            repo_path: Path to the repository to deploy.
            coding_agent: The coding agent to use for generating/fixing scripts.
            filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
            deployment_config: Optional deployment configuration.
            operator_config: Optional operator configuration for timeouts.
            recorder: Optional trajectory recorder.
            dspy_config: Optional DSPy configuration for optimized prompts.
            ui: Optional UI interface.
        """
        self.repo_path = repo_path
        self.agent = coding_agent
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.deployment_config = deployment_config or DeploymentConfig()
        self.operator_config = operator_config or OperatorConfig()
        self.recorder = recorder or NullTrajectoryRecorder()
        self.dspy_config = dspy_config
        self.ui = ui or NullOperatorUI()
        self.sds_dir = self.repo_path / ".sds"
        self.deploy_script = self.sds_dir / "deploy.sh"
        self.health_check_script = self.sds_dir / "health_check.sh"

    def _get_time(self) -> float:
        """Get current time. Separate method to allow mocking in tests."""
        return time.time()

    def _sleep(self, seconds: float) -> None:
        """Sleep for given seconds. Separate method to allow mocking in tests."""
        time.sleep(seconds)

    def _get_next_attempt_number(self) -> int:
        """Determine the next attempt number based on existing logs."""
        logs_dir = self.sds_dir / "logs"
        if not self.filesystem.exists(logs_dir):
            return 1

        # Find all deploy logs
        log_files = list(logs_dir.glob("deploy_attempt_*.log"))
        if not log_files:
            return 1

        # Extract numbers
        max_attempt = 0
        for log_file in log_files:
            try:
                # filename format: deploy_attempt_{n}.log
                name = log_file.stem  # deploy_attempt_{n}
                parts = name.split("_")
                if len(parts) >= 3 and parts[-1].isdigit():
                    num = int(parts[-1])
                    if num > max_attempt:
                        max_attempt = num
            except ValueError:
                continue

        return max_attempt + 1

    def run(
        self, max_attempts: int = 5, check_shutdown: Optional[Callable[[], bool]] = None
    ) -> bool:
        """Attempt deployment with automatic error fixing using a coding agent.
        Ensures scripts exist before deployment.

        Args:
            max_attempts: Maximum number of deployment attempts (default: 5).
            check_shutdown: Optional callable that returns True if shutdown is requested.

        Returns:
            bool: True if deployment succeeded.
        """
        # Step 1: Ensure scripts exist
        if not (
            self.filesystem.exists(self.deploy_script)
            and self.filesystem.exists(self.health_check_script)
        ):
            self.ui.set_stage("Script Generation")
            logger.info("Generating Deployment Scripts")
            logger.info(
                f"Scripts not found in {self.sds_dir}, generating with "
                f"{self.agent.__class__.__name__}..."
            )

            success, message = generate_scripts(
                str(self.repo_path),
                self.agent,
                self.filesystem,
                self.deployment_config,
                self.operator_config,
                recorder=self.recorder,
                dspy_config=self.dspy_config,
            )

            if success:
                logger.success(message)
            else:
                logger.error(message)
                return False
        else:
            logger.success(f"Found existing scripts in {self.sds_dir}")

        # Determine start attempt based on existing logs
        start_attempt = self._get_next_attempt_number()

        # If starting fresh, ensure clean slate for summary
        if start_attempt == 1 and self.operator_config.phase.fix_summary_consolidation:
            summary_file = get_fix_summary_path(self.sds_dir)
            if self.filesystem.exists(summary_file):
                self.filesystem.remove(summary_file)

        end_of_range = start_attempt + max_attempts
        absolute_max_attempts = end_of_range - 1

        # Step 2: Deploy with fixing
        logger.info("Deploying Application with Error Fixing")
        logger.info(
            f"Max attempts: {max_attempts} (Starting from #{start_attempt}, up to #{absolute_max_attempts})"
        )

        for attempt in range(start_attempt, end_of_range):
            if check_shutdown and check_shutdown():
                logger.info("Shutdown requested, aborting deployment")
                return False

            self.ui.set_stage(
                "Deployment", detail=f"Attempt {attempt}/{absolute_max_attempts}"
            )
            logger.info(f"--- Deployment Attempt #{attempt} ---")

            result = self._run_single_attempt(
                attempt, max_attempts, absolute_max_attempts, check_shutdown
            )
            if result is not None:
                return result

        return False

    def _run_single_attempt(
        self,
        attempt: int,
        max_attempts: int,
        absolute_max_attempts: int,
        check_shutdown: Optional[Callable[[], bool]],
    ) -> Optional[bool]:
        """Execute a single deployment attempt.

        Args:
            attempt: Current attempt number.
            max_attempts: Maximum number of attempts (for trajectory metadata).
            absolute_max_attempts: Absolute max attempt number (for fix logic).
            check_shutdown: Optional callable that returns True if shutdown is requested.

        Returns:
            True if deployment succeeded, False if deployment failed permanently,
            None if another attempt should be made.
        """
        with self.recorder.phase(
            Phase.DEPLOYMENT, {
                "attempt": attempt, "max_attempts": max_attempts}
        ) as r:
            # Setup log file for this attempt
            log_file_path = self.sds_dir / "logs" / \
                f"deploy_attempt_{attempt}.log"
            self.filesystem.mkdir(
                log_file_path.parent,
                parents=True,
                exist_ok=True)

            # Run deployment script
            start_time = time.time()
            deploy_result = self.run_deploy_command(
                "start", log_file_path=log_file_path, check_shutdown=check_shutdown
            )
            deploy_duration = time.time() - start_time

            if check_shutdown and check_shutdown():
                logger.info("Shutdown requested, aborting deployment")
                return False

            # Record the deployment tool call
            deploy_ec = deploy_result.get("exit_code")
            r.add_tool_call(
                tool="bash",
                args={"script": ".sds/deploy.sh start"},
                stdout=deploy_result.get("stdout", ""),
                stderr=deploy_result.get("stderr", ""),
                exit_code=int(deploy_ec) if deploy_ec is not None else -1,
                duration=deploy_duration,
            )

            # Check if deployment succeeded
            if deploy_result["success"]:
                logger.success("Deployment script succeeded (exit code: 0)")
                r.add_assistant_message(
                    "Deployment script executed successfully (exit code: 0)",
                    duration=deploy_duration,
                )

                return self._run_health_check_with_retry(
                    deploy_result, attempt, absolute_max_attempts,
                    log_file_path, r,
                )
            else:
                res = deploy_result["exit_code"]
                logger.error(f"Deployment script failed (exit code: {res})")
                r.add_assistant_message(
                    f"Deployment script failed (exit code: {res}). Analyzing errors..."
                )

                # Deployment failed - ask agent to analyze and fix
                if self._fix_with_agent(
                    deploy_result,
                    None,
                    attempt,
                    absolute_max_attempts,
                    log_file_path,
                ):
                    r.set_phase_status("needs_retry")
                else:
                    if attempt < absolute_max_attempts:
                        logger.warning(
                            "Agent failed to fix (or crashed), but retrying..."
                        )
                        r.set_phase_status("needs_retry")
                    else:
                        r.set_phase_status("failed")
                        return False

        return None

    def _run_health_check_with_retry(
        self,
        deploy_result: Dict[str, Any],
        attempt: int,
        absolute_max_attempts: int,
        log_file_path: Path,
        r: TrajectoryRecorderProtocol,
    ) -> Optional[bool]:
        """Run health check and, on failure, attempt agent fix with recheck.

        Args:
            deploy_result: Result from the deployment script.
            attempt: Current attempt number.
            absolute_max_attempts: Absolute max attempt number.
            log_file_path: Path to the deployment log file.
            r: Trajectory recorder for the current phase.

        Returns:
            True if health check passed, False if permanently failed,
            None if another attempt should be made.
        """
        # Setup log file for health check
        health_check_log_path = (
            self.sds_dir / "logs" / f"health_check_attempt_{attempt}.log"
        )
        self.filesystem.mkdir(
            health_check_log_path.parent, parents=True, exist_ok=True
        )

        # Verify with health check
        health_start = time.time()
        health_result = run_health_check(
            self.repo_path,
            self.health_check_script,
            log_file_path=health_check_log_path,
        )
        health_duration = time.time() - health_start

        # Record health check tool call
        health_ec = health_result.get("exit_code")
        r.add_tool_call(
            tool="bash",
            args={"script": ".sds/health_check.sh"},
            stdout=health_result.get("stdout", ""),
            stderr=health_result.get("stderr", ""),
            exit_code=int(health_ec) if health_ec is not None else -1,
            duration=health_duration,
        )

        if health_result["success"]:
            logger.success("Health check passed (exit code: 0)")
            logger.success("Deployment Successful!")
            r.add_assistant_message(
                "Health check passed. Deployment successful!"
            )
            return True

        res = health_result["exit_code"]
        logger.warning(f"Health check failed (exit code: {res})")
        r.add_assistant_message(
            f"Health check failed (exit code: {res}). Analyzing errors..."
        )

        # Health check failed - ask agent to analyze and fix
        if self._fix_with_agent(
            deploy_result,
            health_result,
            attempt,
            absolute_max_attempts,
            log_file_path,
            health_check_log_path,
        ):
            # Re-run health check before committing to a full
            # re-deploy.  If the agent only fixed health_check.sh
            # (e.g. wrong service names) the containers are already
            # healthy and a restart would be wasteful.
            recheck_log = (
                self.sds_dir
                / "logs"
                / f"health_recheck_attempt_{attempt}.log"
            )
            recheck = run_health_check(
                self.repo_path,
                self.health_check_script,
                log_file_path=recheck_log,
            )
            recheck_ec = recheck.get("exit_code")
            r.add_tool_call(
                tool="bash",
                args={
                    "script": ".sds/health_check.sh (post-fix recheck)"
                },
                stdout=recheck.get("stdout", ""),
                stderr=recheck.get("stderr", ""),
                exit_code=int(recheck_ec)
                if recheck_ec is not None
                else -1,
            )

            if recheck["success"]:
                logger.success(
                    "Health check passed after agent fix. Deployment successful!"
                )
                r.add_assistant_message(
                    "Health check passed after agent fix. Deployment successful!"
                )
                return True

            r.set_phase_status("needs_retry")
        else:
            if attempt < absolute_max_attempts:
                logger.warning(
                    "Agent failed to fix (or crashed), but retrying..."
                )
                r.set_phase_status("needs_retry")
            else:
                r.set_phase_status("failed")
                return False

        return None

    def run_deploy_command(
        self,
        command: str = "start",
        timeout: Optional[int] = None,
        log_file_path: Optional[Path] = None,
        check_shutdown: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
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
            timeout = self.operator_config.deploy_timeout
        logger.info(
            f"Running deployment script: {
                self.deploy_script} {command}")

        if log_file_path:
            logger.info(f"Logging output to: {log_file_path}")

        # Create subprocess runner
        # Pass subprocess.Popen from this module so tests can patch it
        runner = SubprocessRunner(
            command=[str(self.deploy_script), command],
            cwd=str(self.repo_path),
            timeout=timeout,
            log_file_path=log_file_path,
            check_shutdown=check_shutdown,
            time_func=self._get_time,
            sleep_func=self._sleep,
            popen_func=subprocess.Popen,
            ui=self.ui,
            tool_name="deploy.sh",
            tool_args={"command": command},
        )

        # Create progress summarizer
        summarizer = ProgressSummarizer(
            agent_generate_fn=lambda prompt, silent, timeout: self.agent.generate(
                prompt, silent=silent, timeout=timeout
            ),
            initial_delay=15.0,
            summary_interval=30.0,
            time_func=self._get_time,
            recorder=self.recorder,
        )

        # Start the subprocess with progress monitoring
        result = runner.run_with_progress_monitoring(summarizer)

        # Log completion
        status = "SUCCESS" if result.get("success") else "FAILED"
        exit_code = result.get("exit_code", -1)
        logger.info(
            f"Deployment command '{command}' finished: {status} (Exit Code: {exit_code})"
        )

        return result

    def _update_consolidated_summary(
        self, current_attempt: int, current_summary: str
    ) -> None:
        """Consolidate fix summaries into a markdown file using the agent."""
        if current_attempt % FIX_SUMMARY_CONSOLIDATION_INTERVAL != 0:
            return

        summary_file = get_fix_summary_path(self.sds_dir)

        # Read existing content if file exists
        existing_content = ""
        if self.filesystem.exists(summary_file):
            existing_content = self.filesystem.read_text(summary_file)

        # Determine range of attempts to consolidate
        start_index = current_attempt - FIX_SUMMARY_CONSOLIDATION_INTERVAL + 1

        # Collect new attempts text
        new_attempts_list = []
        for i in range(start_index, current_attempt + 1):
            if i == current_attempt:
                content = current_summary
            else:
                # Read from log file
                log_path = self.sds_dir / "logs" / f"fix_summary_{i}.log"
                if self.filesystem.exists(log_path):
                    content = self.filesystem.read_text(log_path)
                else:
                    content = "No summary available."

            new_attempts_list.append(f"## Attempt {i}\n{content}\n")

        new_attempts_text = "\n".join(new_attempts_list)

        # Import locally to avoid circular imports if any
        from app_operator.prompts.deployer import create_consolidation_prompt

        prompt = create_consolidation_prompt(
            existing_content, new_attempts_text)

        logger.info("Consolidating fix summaries with agent...")
        try:
            # Use a shorter timeout for summarization
            consolidated_summary_raw = self.agent.generate(
                prompt,
                cwd=str(self.repo_path),
                timeout=self.operator_config.agent_timeout,
                silent=True
            )

            # Extract from <summary> tags
            match = re.search(r"<summary>(.*?)</summary>",
                              consolidated_summary_raw, re.DOTALL)
            if match:
                consolidated_summary = match.group(1).strip()
            else:
                # Fallback to raw output if no tags found
                consolidated_summary = consolidated_summary_raw.strip()

            self.filesystem.write_text(summary_file, consolidated_summary)
            logger.info(f"Updated consolidated summary at {summary_file}")

        except Exception as e:
            logger.warning(f"Failed to consolidate summary: {e}")
            # Fallback: append, but cap to prevent unbounded growth
            _max_fallback = 20_000  # characters
            if existing_content:
                if len(existing_content) > _max_fallback:
                    existing_content = existing_content[-_max_fallback:]
                fallback_content = existing_content + "\n\n" + new_attempts_text
            else:
                fallback_content = new_attempts_text
            self.filesystem.write_text(summary_file, fallback_content)

    def _fix_with_agent(
        self,
        deploy_result: Dict[str, Any],
        health_result: Optional[Dict[str, Any]],
        attempt: int,
        max_attempts: int,
        log_file_path: Optional[Path] = None,
        health_check_log_path: Optional[Path] = None,
    ) -> bool:
        """Use a coding agent to analyze errors and fix the scripts.

        Args:
            deploy_result: Deployment script result.
            health_result: Health check result (None if deployment failed before health check).
            attempt: Current attempt number.
            max_attempts: Maximum number of attempts.
            log_file_path: Path to the deployment log file.
            health_check_log_path: Path to the health check log file.

        Returns:
            bool: True if agent suggested a fix and applied it.
        """
        if attempt >= max_attempts:
            logger.error(
                f"Reached maximum attempts ({max_attempts}), giving up")
            return False

        self.ui.set_stage(
            "Fixing Deployment Issues", detail=f"Attempt {attempt}/{max_attempts}"
        )
        logger.info(
            f"Asking {
                self.agent.__class__.__name__} to Fix Deployment Issues")

        # Prepare error context
        error_context = prepare_error_context(
            deploy_result, health_result, log_file_path, health_check_log_path
        )

        # Create fix prompt
        prompt = create_fix_prompt(
            self.repo_path,
            attempt,
            max_attempts,
            error_context,
            self.deploy_script,
            self.health_check_script,
            dspy_config=self.dspy_config,
            recorder=self.recorder,
            fix_summary_consolidation=self.operator_config.phase.fix_summary_consolidation,
        )

        try:
            logger.info(
                f"Consulting {
                    self.agent.__class__.__name__} to analyze and fix the issue..."
            )

            # Run agent to get fix suggestions
            # Note: The agent is expected to modify files directly
            start_time = time.time()
            response = self.agent.generate(
                prompt,
                cwd=str(self.repo_path),
                timeout=self.operator_config.agent_fix_timeout,
            )
            duration = time.time() - start_time
            logger.info(
                f"Agent generation (fix) took {
                    duration / 60:.2f} minutes")

            # Extract summary and save to log
            match = re.search(r"<summary>(.*?)</summary>", response, re.DOTALL)
            if match:
                summary_text = match.group(1).strip()
            else:
                # Fallback: use the full response or a truncated version as
                # summary
                logger.warning(
                    f"Agent did not provide summary in expected format for attempt {attempt}")
                summary_text = f"Agent attempted to fix deployment issues (no structured summary provided).\n\nFull response:\n{response}"
                # Optionally truncate if too long
                if len(summary_text) > 2000:
                    summary_text = summary_text[:1900] + \
                        "...\n[Response truncated]"

            # Always save some summary
            log_file = self.sds_dir / "logs" / f"fix_summary_{attempt}.log"
            self.filesystem.mkdir(log_file.parent, parents=True, exist_ok=True)
            self.filesystem.write_text(log_file, summary_text)
            logger.info(f"Saved fix summary to {log_file}")

            # Update consolidated summary if enabled
            if self.operator_config.phase.fix_summary_consolidation:
                self._update_consolidated_summary(attempt, summary_text)

            logger.info("Agent response received")

            # Agent should have modified the scripts directly
            # Just notify user and continue to next attempt
            logger.success(
                "Agent has analyzed the issue and may have modified the scripts"
            )
            logger.info("Proceeding to next deployment attempt...")

            return True

        except AgentError as e:
            logger.error(f"Agent failed to provide fix: {e}")
            self.recorder.add_assistant_message(f"Failed to provide fix: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error while getting fix from agent: {e}")
            self.recorder.add_assistant_message(
                f"Unexpected error during fix attempt: {e}"
            )
            return False
