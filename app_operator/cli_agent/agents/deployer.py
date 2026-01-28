import re
import subprocess
import time
from pathlib import Path
from typing import Optional, Callable, Dict, Any

from app_operator.cli_agent.backend.base import CodingAgent
from app_operator.cli_agent.backend.factory import create_agent_from_config
from app_operator.config import DeploymentConfig, OperatorConfig
from app_operator.exceptions import AgentError, DeploymentError, FileSystemError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.prompts import get_loader
from app_operator.deployment_context import analyze_repository, create_system_prompt
from app_operator.cli_agent.subprocess_runner import SubprocessRunner
from app_operator.cli_agent.progress_summarizer import ProgressSummarizer
from tools.healthcheck import run_health_check
from tools.trajectory import (
    Phase,
    record_phase_start,
    record_phase_end,
    record_assistant_message,
    record_tool_call,
)


# Constants
AGENT_FIX_TIMEOUT_SECS = 1800
DEFAULT_DEPLOY_TIMEOUT_SECS = 900
DEFAULT_AGENT_TIMEOUT_SECS = 300


def generate_scripts(
    target_dir: str,
    agent: Optional[CodingAgent] = None,
    filesystem: Optional[FileSystemInterface] = None,
    deployment_config: Optional[DeploymentConfig] = None,
    operator_config: Optional[OperatorConfig] = None,
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

    Returns:
        Tuple of (success: bool, message: str).
    """
    if filesystem is None:
        filesystem = RealFilesystem()

    if deployment_config is None:
        deployment_config = DeploymentConfig()

    if operator_config is None:
        operator_config = OperatorConfig()

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

    # Create .sds directory if it doesn't exist
    sds_dir = target_path / ".sds"
    filesystem.mkdir(sds_dir, exist_ok=True)

    # Get absolute path for context
    abs_target_dir = str(target_path)

    try:
        # Start script generation phase in trajectory
        record_phase_start(Phase.SCRIPT_GENERATION)

        # Create system prompt based on deployment platform
        system_prompt = create_system_prompt(deployment_config.platform)

        # Analyze the repository structure
        repo_context = analyze_repository(target_path)

        # Generate deploy.sh using coding agent
        deploy_success, deploy_msg = _generate_deploy_script(
            agent,
            system_prompt,
            repo_context,
            abs_target_dir,
            filesystem,
            deployment_config,
            operator_config,
        )

        if not deploy_success:
            record_phase_end("failed")
            return False, f"Failed to generate deploy.sh: {deploy_msg}"

        # Generate health_check.sh using coding agent
        health_check_success, health_check_msg = _generate_health_check_script(
            agent,
            system_prompt,
            repo_context,
            abs_target_dir,
            filesystem,
            deployment_config,
            operator_config,
        )

        if not health_check_success:
            record_phase_end("failed")
            return False, f"Failed to generate health_check.sh: {health_check_msg}"

        # Make scripts executable
        deploy_script_path = sds_dir / "deploy.sh"
        health_check_script_path = sds_dir / "health_check.sh"

        if filesystem.exists(deploy_script_path):
            filesystem.chmod(deploy_script_path, 0o755)

        if filesystem.exists(health_check_script_path):
            filesystem.chmod(health_check_script_path, 0o755)

        record_phase_end("success")
        return True, f"Successfully generated scripts in {sds_dir}"

    except (AgentError, DeploymentError, FileSystemError) as e:
        record_phase_end("failed")
        return False, f"Failed to generate scripts: {e}"
    except Exception as e:
        # Catch any unexpected errors and log them
        record_phase_end("failed")
        return False, f"Unexpected error during script generation: {e}"


def _generate_deploy_script(
    agent: CodingAgent,
    system_prompt: str,
    repo_context: str,
    target_dir: str,
    filesystem: FileSystemInterface,
    deployment_config: Optional[DeploymentConfig] = None,
    operator_config: Optional[OperatorConfig] = None,
) -> tuple[bool, str]:
    """Generate deploy.sh script using a coding agent."""
    if operator_config is None:
        operator_config = OperatorConfig()

    platform = deployment_config.platform if deployment_config else "auto"
    full_prompt = get_loader().render(
        "deployer/generate_script.jinja2",
        system_prompt=system_prompt,
        script_name="deploy.sh",
        repo_context=repo_context,
        target_dir=target_dir,
        platform=platform,
    )

    try:
        start_time = time.time()
        agent.generate(
            full_prompt, cwd=target_dir, timeout=operator_config.agent_timeout
        )

        duration = time.time() - start_time
        logger.info(f"Agent generation took {duration / 60:.2f} minutes")

        deploy_script_path = Path(target_dir) / ".sds" / "deploy.sh"
        if filesystem.exists(deploy_script_path):
            return True, "Successfully generated deploy.sh"
        else:
            return False, "Agent failed to create .sds/deploy.sh"

    except subprocess.TimeoutExpired:
        timeout = operator_config.agent_timeout // 60
        record_assistant_message(f"Script generation timed out after {timeout} minutes")
        return (
            False,
            f"agent command timed out after {timeout} minutes",
        )
    except AgentError as e:
        record_assistant_message(f"Script generation failed: {e}")
        return False, str(e)
    except Exception as e:
        record_assistant_message(f"Script generation failed: {e}")
        return False, str(e)


def _generate_health_check_script(
    agent: CodingAgent,
    system_prompt: str,
    repo_context: str,
    target_dir: str,
    filesystem: FileSystemInterface,
    deployment_config: Optional[DeploymentConfig] = None,
    operator_config: Optional[OperatorConfig] = None,
) -> tuple[bool, str]:
    """Generate health_check.sh script using a coding agent."""
    if operator_config is None:
        operator_config = OperatorConfig()

    platform = deployment_config.platform if deployment_config else "auto"
    full_prompt = get_loader().render(
        "deployer/generate_script.jinja2",
        system_prompt=system_prompt,
        script_name="health_check.sh",
        repo_context=repo_context,
        target_dir=target_dir,
        platform=platform,
    )

    try:
        start_time = time.time()
        agent.generate(
            full_prompt, cwd=target_dir, timeout=operator_config.agent_timeout
        )

        duration = time.time() - start_time
        logger.info(f"Agent generation took {duration / 60:.2f} minutes")

        health_check_script_path = Path(target_dir) / ".sds" / "health_check.sh"
        if filesystem.exists(health_check_script_path):
            return True, "Successfully generated health_check.sh"
        else:
            return False, "Agent failed to create .sds/health_check.sh"

    except subprocess.TimeoutExpired:
        timeout = operator_config.agent_timeout // 60
        record_assistant_message(f"Script generation timed out after {timeout} minutes")
        return (
            False,
            f"agent command timed out after {timeout} minutes",
        )
    except AgentError as e:
        record_assistant_message(f"Script generation failed: {e}")
        return False, str(e)
    except Exception as e:
        record_assistant_message(f"Script generation failed: {e}")
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
    ):
        """Initialize the deployment agent.

        Args:
            repo_path: Path to the repository to deploy.
            coding_agent: The coding agent to use for generating/fixing scripts.
            filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
            deployment_config: Optional deployment configuration.
            operator_config: Optional operator configuration for timeouts.
        """
        self.repo_path = repo_path
        self.agent = coding_agent
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.deployment_config = deployment_config or DeploymentConfig()
        self.operator_config = operator_config or OperatorConfig()
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

            logger.info(f"--- Deployment Attempt #{attempt} ---")

            # Start deployment phase in trajectory
            record_phase_start(
                Phase.DEPLOYMENT, {"attempt": attempt, "max_attempts": max_attempts}
            )

            # Setup log file for this attempt
            log_file_path = self.sds_dir / "logs" / f"deploy_attempt_{attempt}.log"
            # Ensure directory exists
            self.filesystem.mkdir(log_file_path.parent, parents=True, exist_ok=True)

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
            record_tool_call(
                tool="bash",
                args={"script": ".sds/deploy.sh start"},
                stdout=deploy_result.get("stdout", ""),
                stderr=deploy_result.get("stderr", ""),
                exit_code=int(deploy_result.get("exit_code", -1) or -1),
                duration=deploy_duration,
            )

            # Check if deployment succeeded
            if deploy_result["success"]:
                logger.success("Deployment script succeeded (exit code: 0)")
                record_assistant_message(
                    "Deployment script executed successfully (exit code: 0)",
                    duration=deploy_duration,
                )

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
                record_tool_call(
                    tool="bash",
                    args={"script": ".sds/health_check.sh"},
                    stdout=health_result.get("stdout", ""),
                    stderr=health_result.get("stderr", ""),
                    exit_code=int(health_result.get("exit_code", -1) or -1),
                    duration=health_duration,
                )

                if health_result["success"]:
                    logger.success("Health check passed (exit code: 0)")
                    logger.success("Deployment Successful!")
                    record_assistant_message(
                        "Health check passed. Deployment successful!"
                    )
                    record_phase_end("success")
                    return True
                else:
                    res = health_result["exit_code"]
                    logger.warning(f"Health check failed (exit code: {res})")
                    record_assistant_message(
                        f"Health check failed (exit code: {res}). Analyzing errors..."
                    )

                    # Health check failed - ask agent to analyze and fix
                    if not self._fix_with_agent(
                        deploy_result,
                        health_result,
                        attempt,
                        absolute_max_attempts,
                        log_file_path,
                        health_check_log_path,
                    ):
                        record_phase_end("failed")
                        return False
                    record_phase_end("needs_retry")
            else:
                res = deploy_result["exit_code"]
                logger.error(f"Deployment script failed (exit code: {res})")
                record_assistant_message(
                    f"Deployment script failed (exit code: {res}). Analyzing errors..."
                )

                # Deployment failed - ask agent to analyze and fix
                if not self._fix_with_agent(
                    deploy_result, None, attempt, absolute_max_attempts, log_file_path
                ):
                    record_phase_end("failed")
                    return False
                record_phase_end("needs_retry")

        return False

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
        logger.info(f"Running deployment script: {self.deploy_script} {command}")

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
        )

        # Create progress summarizer
        summarizer = ProgressSummarizer(
            agent_generate_fn=lambda prompt, silent, timeout: self.agent.generate(
                prompt, silent=silent, timeout=timeout
            ),
            initial_delay=15.0,
            summary_interval=30.0,
            time_func=self._get_time,
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
            logger.error(f"Reached maximum attempts ({max_attempts}), giving up")
            return False

        logger.info(f"Asking {self.agent.__class__.__name__} to Fix Deployment Issues")

        # Prepare error context
        error_context = self._prepare_error_context(
            deploy_result, health_result, log_file_path, health_check_log_path
        )

        # Create fix prompt
        prompt = self._create_fix_prompt(error_context, attempt, max_attempts)

        try:
            logger.info(
                f"Consulting {self.agent.__class__.__name__} to analyze and fix the issue..."
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
            logger.info(f"Agent generation (fix) took {duration / 60:.2f} minutes")

            # Extract summary and save to log
            match = re.search(r"<summary>(.*?)</summary>", response, re.DOTALL)
            if match:
                summary_text = match.group(1).strip()
                log_file = self.sds_dir / "logs" / f"fix_summary_{attempt}.log"
                self.filesystem.mkdir(log_file.parent, parents=True, exist_ok=True)
                self.filesystem.write_text(log_file, summary_text)
                logger.info(f"Saved fix summary to {log_file}")

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
            record_assistant_message(f"Failed to provide fix: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error while getting fix from agent: {e}")
            record_assistant_message(f"Unexpected error during fix attempt: {e}")
            return False

    def _prepare_error_context(
        self,
        deploy_result: Dict[str, Any],
        health_result: Optional[Dict[str, Any]],
        log_file_path: Optional[Path] = None,
        health_check_log_path: Optional[Path] = None,
    ) -> str:
        """Prepare error context for the coding agent.

        Args:
            deploy_result: Deployment script result.
            health_result: Health check result (None if deployment failed).
            log_file_path: Path to the deployment log file.
            health_check_log_path: Path to the health check log file.

        Returns:
            str: Formatted error context.
        """
        context_parts = []

        if log_file_path:
            context_parts.append(f"Full deployment logs available at: {log_file_path}")

        if health_check_log_path:
            context_parts.append(
                f"Health check outputs available at: {health_check_log_path}"
            )

        # Deployment result
        context_parts.append("## Deployment Script Result")
        context_parts.append(f"Exit Code: {deploy_result['exit_code']}")
        context_parts.append(
            f"Status: {'SUCCESS' if deploy_result['success'] else 'FAILED'}"
        )

        if health_result is not None:
            context_parts.append("\n## Health Check Result")
            context_parts.append(f"Exit Code: {health_result['exit_code']}")
            context_parts.append(
                f"Status: {'SUCCESS' if health_result['success'] else 'FAILED'}"
            )

        return "\n".join(context_parts)

    def _create_fix_prompt(
        self, error_context: str, attempt: int, max_attempts: int
    ) -> str:
        """Create a prompt for the coding agent to fix deployment errors.

        Args:
            error_context: Formatted error context.
            attempt: Current attempt number.
            max_attempts: Maximum number of attempts.

        Returns:
            str: The prompt for the agent.
        """
        # Determine previous fix summary file path
        previous_summary_note = ""
        if attempt > 1:
            prev_log_path = self.sds_dir / "logs" / f"fix_summary_{attempt - 1}.log"
            previous_summary_note = (
                f"\n\nNote: This is attempt #{attempt}. "
                f"You can read the summary of the previous fix attempt at:\n{prev_log_path}\n"
                "The log files follow the pattern .sds/logs/fix_summary_{attempt}.log. "
                "Please review the previous attempt to avoid repeating mistakes, and to check if the previous fix was successful."
                "Note that the application may still be failing, but the it's now failing for a different reason."
            )

        return get_loader().render(
            "deployer/fix_error.jinja2",
            repo_path=self.repo_path,
            attempt=attempt,
            max_attempts=max_attempts,
            error_context=error_context,
            previous_summary_note=previous_summary_note,
            deploy_script=self.deploy_script,
            health_check_script=self.health_check_script,
        )
