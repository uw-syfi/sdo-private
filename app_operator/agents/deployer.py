import subprocess
import time
import threading
import re
from pathlib import Path
from typing import Optional, Callable, Dict, Any

from app_operator.agent_cli.base import CodingAgent
from app_operator.agent_cli.factory import create_agent_from_config
from app_operator.config import DeploymentConfig
from app_operator.exceptions import AgentError, DeploymentError, FileSystemError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.prompts import get_loader
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

    Returns:
        Tuple of (success: bool, message: str).
    """
    if filesystem is None:
        filesystem = RealFilesystem()

    if deployment_config is None:
        deployment_config = DeploymentConfig()

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
        system_prompt = _create_system_prompt(deployment_config.platform)

        # Analyze the repository structure
        repo_context = _analyze_repository(target_path)

        # Generate deploy.sh using coding agent
        deploy_success, deploy_msg = _generate_deploy_script(
            agent,
            system_prompt,
            repo_context,
            abs_target_dir,
            filesystem,
            deployment_config,
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


def _create_system_prompt(platform: str) -> str:
    """Create the system prompt for coding agent to guide script generation."""
    return get_loader().render("deployer/system.jinja2", platform=platform)


def _analyze_repository(repo_path: Path) -> str:
    """Analyze repository structure and return context string."""
    context_parts = []

    # Check for common deployment files
    if (repo_path / ".sds" / "code_analysis.md").exists():
        context_parts.append("- Found code analysis: .sds/code_analysis.md")
    if (repo_path / ".sds" / "deployment_issues.md").exists():
        context_parts.append(
            "- Found deployment issues report: .sds/deployment_issues.md"
        )

    if (repo_path / "docker-compose.yml").exists():
        context_parts.append("- Found docker-compose.yml (Docker Compose deployment)")
    if (repo_path / "docker-compose.yaml").exists():
        context_parts.append("- Found docker-compose.yaml (Docker Compose deployment)")
    if (repo_path / "Dockerfile").exists():
        context_parts.append("- Found Dockerfile (Docker-based application)")
    if (repo_path / "k8s").exists() or (repo_path / "kubernetes").exists():
        context_parts.append("- Found Kubernetes manifests directory")
    if (repo_path / "Makefile").exists():
        context_parts.append("- Found Makefile (may contain build/deploy targets)")

    # Check for common application files
    if (repo_path / "package.json").exists():
        context_parts.append("- Found package.json (Node.js application)")
    if (repo_path / "requirements.txt").exists() or (
        repo_path / "pyproject.toml"
    ).exists():
        context_parts.append("- Found Python dependencies (Python application)")
    if (repo_path / "go.mod").exists():
        context_parts.append("- Found go.mod (Go application)")
    if (repo_path / "Cargo.toml").exists():
        context_parts.append("- Found Cargo.toml (Rust application)")
    if (repo_path / "pom.xml").exists():
        context_parts.append("- Found pom.xml (Java/Maven application)")

    # Check for README
    readme_files = list(repo_path.glob("README*"))
    if readme_files:
        context_parts.append(
            f"- Found README file(s): {', '.join(f.name for f in readme_files)}"
        )

    # Get repository name
    repo_name = repo_path.name
    context_parts.insert(0, f"Repository: {repo_name}")

    return (
        "\n".join(context_parts)
        if context_parts
        else "Repository structure analysis: No obvious deployment files found"
    )


def _generate_deploy_script(
    agent: CodingAgent,
    system_prompt: str,
    repo_context: str,
    target_dir: str,
    filesystem: FileSystemInterface,
    deployment_config: Optional[DeploymentConfig] = None,
) -> tuple[bool, str]:
    """Generate deploy.sh script using a coding agent."""
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
        agent.generate(full_prompt, cwd=target_dir, timeout=DEFAULT_AGENT_TIMEOUT_SECS)

        duration = time.time() - start_time
        logger.info(f"Agent generation took {duration / 60:.2f} minutes")

        deploy_script_path = Path(target_dir) / ".sds" / "deploy.sh"
        if filesystem.exists(deploy_script_path):
            return True, "Successfully generated deploy.sh"
        else:
            return False, "Agent failed to create .sds/deploy.sh"

    except subprocess.TimeoutExpired:
        timeout = DEFAULT_AGENT_TIMEOUT_SECS // 60
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
) -> tuple[bool, str]:
    """Generate health_check.sh script using a coding agent."""
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
        agent.generate(full_prompt, cwd=target_dir, timeout=DEFAULT_AGENT_TIMEOUT_SECS)

        duration = time.time() - start_time
        logger.info(f"Agent generation took {duration / 60:.2f} minutes")

        health_check_script_path = Path(target_dir) / ".sds" / "health_check.sh"
        if filesystem.exists(health_check_script_path):
            return True, "Successfully generated health_check.sh"
        else:
            return False, "Agent failed to create .sds/health_check.sh"

    except subprocess.TimeoutExpired:
        timeout = DEFAULT_AGENT_TIMEOUT_SECS // 60
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
    ):
        """Initialize the deployment agent.

        Args:
            repo_path: Path to the repository to deploy.
            coding_agent: The coding agent to use for generating/fixing scripts.
            filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
            deployment_config: Optional deployment configuration.
        """
        self.repo_path = repo_path
        self.agent = coding_agent
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.deployment_config = deployment_config or DeploymentConfig()
        self.sds_dir = self.repo_path / ".sds"
        self.deploy_script = self.sds_dir / "deploy.sh"
        self.health_check_script = self.sds_dir / "health_check.sh"

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
                str(self.repo_path), self.agent, self.filesystem, self.deployment_config
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
        timeout: int = DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path: Optional[Path] = None,
        check_shutdown: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Run the deployment script with a specific command.

        Args:
            command: The command to pass to the script (e.g., "start", "stop").
            timeout: Timeout in seconds.
            log_file_path: Optional path to write output logs to.
            check_shutdown: Optional callable returning True if shutdown requested.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        logger.info(f"Running deployment script: {self.deploy_script} {command}")

        # Open log file if provided
        log_file = None
        log_lock = threading.Lock()
        if log_file_path:
            try:
                log_file = open(log_file_path, "w")
                logger.info(f"Logging output to: {log_file_path}")
            except (OSError, IOError) as e:
                logger.warning(f"Could not open log file {log_file_path}: {e}")

        # Buffers to capture output
        stdout_lines = []
        stderr_lines = []

        def read_pipe(pipe, buffer):
            """Read pipe line by line and capture."""
            try:
                for line in iter(pipe.readline, ""):
                    if not line:
                        break
                    # We don't print here to avoid spamming, unless it's a short command?
                    # The original implementation captured output but didn't print in real-time
                    # except via the subprocess.run return.
                    # But for long commands we might want to see it?
                    # The requirement says "produce a brief summary... Don't just print the agent_cli's outputs"
                    # It implies we rely on the summary.
                    buffer.append(line)

                    if log_file:
                        with log_lock:
                            log_file.write(line)
                            log_file.flush()
            except ValueError:
                pass  # Handle closed file
            finally:
                pipe.close()

        process = None
        try:
            # Run deploy script with command
            process = subprocess.Popen(
                [str(self.deploy_script), command],
                cwd=str(self.repo_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )

            # Start threads to read stdout and stderr
            stdout_thread = threading.Thread(
                target=read_pipe, args=(process.stdout, stdout_lines)
            )
            stderr_thread = threading.Thread(
                target=read_pipe, args=(process.stderr, stderr_lines)
            )

            stdout_thread.daemon = True
            stderr_thread.daemon = True

            stdout_thread.start()
            stderr_thread.start()

            start_time = time.time()
            last_summary_time = start_time
            summary_interval = 30
            initial_delay = 15

            while process.poll() is None:
                current_time = time.time()
                elapsed = current_time - start_time

                # Check shutdown
                if check_shutdown and check_shutdown():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return {
                        "success": False,
                        "exit_code": -1,
                        "stdout": "".join(stdout_lines),
                        "stderr": "Deployment interrupted by shutdown request\n"
                        + "".join(stderr_lines),
                    }

                # Check timeout
                if elapsed > timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return {
                        "success": False,
                        "exit_code": -1,
                        "stdout": "".join(stdout_lines),
                        "stderr": f"Deployment script timed out after {timeout} seconds\n"
                        + "".join(stderr_lines),
                    }

                # Check for summary update
                if (
                    elapsed > initial_delay
                    and (current_time - last_summary_time) >= summary_interval
                ):
                    # Get recent output
                    recent_stdout = "".join(stdout_lines[-20:])
                    recent_stderr = "".join(stderr_lines[-20:])
                    recent_output = f"{recent_stdout}\n{recent_stderr}"

                    if recent_output.strip():
                        self._summarize_progress(recent_output, elapsed)

                    last_summary_time = time.time()

                time.sleep(0.5)

            # Wait for threads to finish
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)

            if log_file:
                log_file.close()

            stdout_data = "".join(stdout_lines)
            stderr_data = "".join(stderr_lines)

            status = "SUCCESS" if process.returncode == 0 else "FAILED"
            logger.info(
                f"Deployment command '{command}' finished: {status} (Exit Code: {
                    process.returncode})"
            )

            return {
                "success": process.returncode == 0,
                "exit_code": process.returncode,
                "stdout": stdout_data,
                "stderr": stderr_data,
            }

        except (OSError, subprocess.SubprocessError) as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Failed to run deployment script: {e}",
            }
        except Exception as e:
            # Catch any unexpected errors
            logger.error(f"Unexpected error running deployment script: {e}")
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Unexpected error running deployment script: {e}",
            }
        finally:
            # Ensure process is killed on exit (including KeyboardInterrupt)
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except (subprocess.TimeoutExpired, Exception):
                    try:
                        process.kill()
                    except Exception:
                        pass

    def _summarize_progress(self, output_snippet: str, elapsed_time: float):
        """Generate and print a summary of the progress using the agent."""
        prompt = get_loader().render(
            "deployer/summarize.jinja2", output_snippet=output_snippet
        )
        try:
            # Use silent=True to avoid printing the agent's internal thought process
            response = self.agent.generate(prompt, silent=True, timeout=30)
            summary = self._extract_summary(response)
            if summary:
                logger.info(f"[{elapsed_time:.1f}s] ➜ {summary}")
        except Exception:
            # If summarization fails, just ignore it to not interrupt the flow
            pass

    def _extract_summary(self, response: str) -> Optional[str]:
        """Extract the summary from the agent's response using XML markers."""
        match = re.search(r"<output_msg>(.*?)</output_msg>", response, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

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
                prompt, cwd=str(self.repo_path), timeout=AGENT_FIX_TIMEOUT_SECS
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
