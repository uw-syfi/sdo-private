import os
import subprocess
import time
import threading
import re
from pathlib import Path
from typing import Optional, Callable, Dict, Any

from app_operator.agent_cli.base import CodingAgent
from app_operator.agent_cli.factory import create_agent_from_config
from app_operator.config import DeploymentConfig
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
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
        except RuntimeError as e:
            return False, str(e)

    # Create .sds directory if it doesn't exist
    sds_dir = target_path / ".sds"
    filesystem.mkdir(sds_dir, exist_ok=True)

    # Get absolute path for context
    abs_target_dir = str(target_path)

    # Change to target directory for context
    original_cwd = os.getcwd()
    try:
        os.chdir(abs_target_dir)

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

    except Exception as e:
        record_phase_end("failed")
        return False, f"Failed to generate scripts: {e}"
    finally:
        # Restore original working directory
        os.chdir(original_cwd)


def _create_system_prompt(platform: str) -> str:
    """Create the system prompt for coding agent to guide script generation."""
    prompt = """You are an expert DevOps engineer generating deployment and health check scripts for applications.

Your task is to analyze a repository and generate two bash scripts:
1. deploy.sh - A comprehensive deployment script
2. health_check.sh - A comprehensive health check script

"""

    if platform == "k8s":
        prompt += """## CRITICAL: Target Platform is Kubernetes

You MUST generate scripts for Kubernetes deployment.

### Kubernetes Platform Requirements:
- Look for: k8s/, kubernetes/, *.yaml manifests with "kind: Deployment", "kind: Service", etc.
- If existing manifests are found, use them.
- If no manifests are found, you MUST generate valid Kubernetes manifests (Deployment, Service) and apply them.
- Generate scripts that use `kubectl` commands
- Commands: kubectl apply, kubectl delete, kubectl get pods, kubectl logs, etc.
- Use `kubectl wait` or check loops for readiness checks

**CRITICAL REQUIREMENT**: The deployment method MUST be Kubernetes. DO NOT use Docker Compose or plain Docker commands.
"""
    elif platform == "docker":
        prompt += """## CRITICAL: Target Platform is Docker

You MUST generate scripts for Docker deployment.

### Docker Platform Requirements:
- Analyze the repository to choose between Docker Compose and plain Docker.
- **Docker Compose** (Recommended if compose file exists):
  - Look for: docker-compose.yml, docker-compose.yaml, compose.yml, compose.yaml
  - If found: Generate scripts that use `docker-compose` or `docker compose` commands
  - Commands: docker-compose up, docker-compose down, docker-compose ps, docker-compose logs
- **Plain Docker**:
  - Look for: Dockerfile(s) without docker-compose files
  - If found: Generate scripts that use direct `docker run` commands
  - Commands: docker build, docker run, docker stop, docker ps, docker logs

**CRITICAL REQUIREMENT**: The deployment method MUST be Docker based. DO NOT use Kubernetes commands.
"""
    else:
        prompt += """## CRITICAL: Deployment Platform Detection

You MUST analyze the repository structure to determine the EXACT deployment platform:

### Docker Compose Platform:
- Look for: docker-compose.yml, docker-compose.yaml, compose.yml, compose.yaml
- If found: Generate scripts that use `docker-compose` or `docker compose` commands

### Kubernetes Platform:
- Look for: k8s/, kubernetes/, *.yaml manifests
- If found: Generate scripts that use `kubectl` commands

### Plain Docker Platform:
- Look for: Dockerfile(s) without docker-compose files
- If found: Generate scripts that use direct `docker run` commands
"""

    prompt += """
## DO's:
- Use proper bash scripting practices (set -e, proper error handling)
- Include color-coded output (RED, GREEN, YELLOW, BLUE, CYAN, MAGENTA, NC)
- Add helper functions for printing (print_header, print_success, print_error, print_warning, print_info)
- Check prerequisites (Docker, Docker Compose, kubectl, etc. based on platform)
- Use SCRIPT_DIR and proper path resolution
- Include comprehensive error handling
- Add command-line argument parsing
- Include help/usage information
- Make scripts executable-ready (shebang #!/bin/bash)
- For deploy.sh: Include start, stop, restart, status, logs, build, cleanup commands
- For health_check.sh: Check containers/pods, ports, endpoints, databases, performance metrics
- Include summary reports and exit codes

## DON'Ts:
- **NEVER mix deployment platforms** (e.g., don't use both docker-compose and kubectl in the same script)
- Don't hardcode absolute paths (use SCRIPT_DIR and relative paths)
- Don't skip error handling
- Don't use deprecated commands
- Don't generate overly complex scripts - keep them maintainable
- Don't assume specific port numbers without checking the codebase
- Don't include hardcoded credentials or secrets
- Don't generate scripts that modify files outside the application directory
- Don't skip prerequisite checks

## Example Structure (ADAPT TO REPOSITORY):

deploy.sh should include:
- Configuration section with paths and env vars
- Helper functions
- check_prerequisites()
- build_images() (if needed)
- start_services()
- stop_services()
- restart_services()
- check_health()
- view_logs()
- cleanup()
- Main command dispatcher

health_check.sh should include:
- Configuration section
- Helper functions
- check_platform_specific_resources()
- check_ports()
- check_endpoints()
- print_summary()
- Main execution flow

## Important:
- **STEP 1**: Confirm the repository structure matches the target platform.
- **STEP 2**: Identify services, ports, and health check endpoints.
- **STEP 3**: Generate robust scripts using the correct commands for the platform.
- **VERIFY**: Explicitly state which platform you are targeting in the comments."""

    return prompt


def _analyze_repository(repo_path: Path) -> str:
    """Analyze repository structure and return context string."""
    context_parts = []

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

    platform_specific_instructions = ""
    if deployment_config and deployment_config.platform == "k8s":
        platform_specific_instructions = """
1. **Target Platform: Kubernetes**
   - Use `kubectl` commands.
   - Ensure you handle manifest application and pod readiness.
"""
    elif deployment_config and deployment_config.platform == "docker":
        platform_specific_instructions = """
1. **Target Platform: Docker**
   - Use `docker-compose` or `docker` commands as appropriate for the repo.
"""
    else:
        platform_specific_instructions = """
1. **Deployment Platform Detection**:
   - Identify if the repo uses Docker Compose, Kubernetes, or plain Docker.
   - Use the appropriate commands.
"""

    human_prompt = f"""Generate a comprehensive deploy.sh bash script for the following repository:

{repo_context}

Target directory: {target_dir}

CRITICAL REQUIREMENTS:

{platform_specific_instructions}

2. **Script Requirements**:
   - Create the file at: .sds/deploy.sh
   - Use SCRIPT_DIR to determine paths relative to the script location
   - Include all standard deployment commands (start, stop, restart, status, logs, build, cleanup)
   - Make it robust, well-documented, and production-ready
   - Include proper error handling and colored output
   - The script should work when executed from the .sds directory
   - Check for correct prerequisites

3. **Platform-Specific Commands**:
   - Use the commands that match the detected/target platform (see System Prompt).

You must use the write_file tool to create the file .sds/deploy.sh directly. Do not just print the content."""

    full_prompt = f"""{system_prompt}

{human_prompt}"""
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

    platform_specific_instructions = ""
    if deployment_config and deployment_config.platform == "k8s":
        platform_specific_instructions = """
1. **Target Platform: Kubernetes**
   - Use `kubectl` commands.
   - Check pod status and readiness.
"""
    elif deployment_config and deployment_config.platform == "docker":
        platform_specific_instructions = """
1. **Target Platform: Docker**
   - Use `docker-compose ps` or `docker ps`.
   - Check container status.
"""
    else:
        platform_specific_instructions = """
1. **Deployment Platform Detection**:
   - Identify if the repo uses Docker Compose, Kubernetes, or plain Docker.
   - Use the appropriate status check commands.
"""

    human_prompt = f"""Generate a comprehensive health_check.sh bash script for the following repository:

{repo_context}

Target directory: {target_dir}

CRITICAL REQUIREMENTS:

{platform_specific_instructions}

2. **Script Requirements**:
   - Create the file at: .sds/health_check.sh
   - Use SCRIPT_DIR to determine paths relative to the script location
   - Include checks for: containers/pods, ports, endpoints, databases, cache, performance
   - Include a summary report with health score
   - Make it robust, well-documented, and production-ready
   - Include proper error handling and colored output
   - Track total checks, passed checks, failed checks, warnings
   - The script should work when executed from the .sds directory

3. **Platform-Specific Checks**:
   - Use the checks that match the detected/target platform (see System Prompt).

You must use the write_file tool to create the file .sds/health_check.sh directly. Do not just print the content."""

    full_prompt = f"""{system_prompt}

{human_prompt}"""

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
                "start", log_file_path=log_file_path
            )
            deploy_duration = time.time() - start_time

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
    ) -> Dict[str, Any]:
        """Run the deployment script with a specific command.

        Args:
            command: The command to pass to the script (e.g., "start", "stop").
            timeout: Timeout in seconds.
            log_file_path: Optional path to write output logs to.

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
            except Exception as e:
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
                f"Deployment command '{command}' finished: {status} (Exit Code: {process.returncode})"
            )

            return {
                "success": process.returncode == 0,
                "exit_code": process.returncode,
                "stdout": stdout_data,
                "stderr": stderr_data,
            }

        except Exception as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Failed to run deployment script: {e}",
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
        prompt = f"""The following is the recent output of a long-running deployment command.
Please provide a brief, one-line summary of what is currently happening.
Wrap your summary in <output_msg>...</output_msg> XML tags.
Do not include any other text or debug info.

Recent Output:
{output_snippet}
"""
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

        except Exception as e:
            logger.error(f"Agent failed to provide fix: {e}")
            record_assistant_message(f"Failed to provide fix: {e}")
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

        # if deploy_result['stdout']:
        #     context_parts.append("\n### STDOUT:")
        #     # Truncate if too long
        #     stdout = deploy_result['stdout']
        #     if len(stdout) > 3000:
        #         stdout = stdout[-3000:]
        #         context_parts.append(
        #             "... (truncated, showing last 3000 chars)")
        #     context_parts.append(stdout)

        # if deploy_result['stderr']:
        #     context_parts.append("\n### STDERR:")
        #     stderr = deploy_result['stderr']
        #     if len(stderr) > 3000:
        #         stderr = stderr[-3000:]
        #         context_parts.append(
        #             "... (truncated, showing last 3000 chars)")
        #     context_parts.append(stderr)

        # # Health check result (if available)
        # if health_result:
        #     context_parts.append("\n## Health Check Result")
        #     context_parts.append(f"Exit Code: {health_result['exit_code']}")
        #     context_parts.append(
        #         f"Status: {'SUCCESS' if health_result['success'] else 'FAILED'}")

        #     if health_result['stdout']:
        #         context_parts.append("\n### STDOUT:")
        #         stdout = health_result['stdout']
        #         if len(stdout) > 3000:
        #             stdout = stdout[-3000:]
        #             context_parts.append(
        #                 "... (truncated, showing last 3000 chars)")
        #         context_parts.append(stdout)

        #     if health_result['stderr']:
        #         context_parts.append("\n### STDERR:")
        #         stderr = health_result['stderr']
        #         if len(stderr) > 3000:
        #             stderr = stderr[-3000:]
        #             context_parts.append(
        #                 "... (truncated, showing last 3000 chars)")
        #         context_parts.append(stderr)

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

        system_prompt = """You are an expert DevOps engineer debugging deployment issues.

Your task is to analyze deployment errors and fix the deployment scripts.

## Context
- Repository: {repo_path}
- Deployment script: .sds/deploy.sh
- Health check script: .sds/health_check.sh
- Current attempt: {attempt} of {max_attempts}

## CRITICAL: Deployment Platform Awareness

**BEFORE MAKING ANY FIXES, IDENTIFY THE DEPLOYMENT PLATFORM:**

1. Read the deployment script (.sds/deploy.sh) to see what commands it uses
2. Identify if it's using:
   - Docker Compose (docker-compose commands)
   - Kubernetes (kubectl commands)
   - Plain Docker (docker run/stop/ps commands)
   - Other platforms

3. **CRITICAL**: All fixes MUST use commands appropriate for the detected platform
   - If using Docker Compose → Use docker-compose commands in fixes
   - If using Kubernetes → Use kubectl commands in fixes
   - If using plain Docker → Use docker commands in fixes
   - DO NOT mix platforms or suggest commands for the wrong platform

4. Check the repository structure to confirm:
   - Look for docker-compose.yml/yaml files
   - Look for Kubernetes manifests (k8s/, *.yaml with kind: Deployment)
   - Look for Dockerfiles

## DO's:
- **FIRST**: Identify the deployment platform being used
- Carefully analyze the error messages and logs
- Identify the root cause of the deployment failure
- Fix the scripts directly using file editing tools
- Consider common issues: missing dependencies, wrong ports, incorrect paths, permission issues
- Test your understanding of the error before making changes
- Make minimal, targeted fixes
- Ensure scripts are still robust after fixes
- Fix both deployment and health check scripts if needed
- Consider environment-specific issues (Docker, Kubernetes, networking, file permissions)
- Look for typos, syntax errors, or incorrect commands
- Check if services are starting in the right order
- Verify port bindings and network configurations
- Check for missing environment variables or configuration files
- **Use platform-appropriate commands** (match the platform detected in the script)

## DON'Ts:
- **NEVER change the deployment platform** (don't switch from docker-compose to kubectl or vice versa)
- **NEVER use commands from the wrong platform** (don't use kubectl if script uses docker-compose)
- Don't make random changes without understanding the error
- Don't remove error handling or safety checks
- Don't introduce new bugs while fixing old ones
- Don't skip analyzing the full error context
- Don't make overly complex changes
- Don't modify files outside .sds directory
- Don't give up easily - try multiple approaches if needed
- Don't assume - verify your assumptions against the error logs
- Don't redeploy the application; propose the fix and let the user decide to redeploy.

## Approach:
1. **STEP 1**: Read .sds/deploy.sh to identify which deployment platform it uses
2. **STEP 2**: Read and analyze the error messages carefully
3. **STEP 3**: Identify the specific failure point
4. **STEP 4**: Determine the root cause
5. **STEP 5**: Make targeted fixes using commands appropriate for the detected platform
6. **STEP 6**: Verify the fix makes sense in context

## Expected Output:
- State which deployment platform you detected (Docker Compose, Kubernetes, plain Docker, etc.)
- Analyze the error thoroughly
- Explain what went wrong
- Describe the fix you're applying
- Make the necessary changes to the scripts (using correct platform commands)
- Confirm the changes are complete
- Output a brief summary of the fix wrapped in <summary></summary> tags.
  - The summary must explicitly state:
    1) what were the issue(s) found
    2) what were your fix(es)
    3) which deployment platform is being used
  - Be concise but thorough in coverage.

## Deployment tooling

If the deployment script uses Docker or Docker Compose, you can run docker/docker-compose commands directly to inspect the container status and logs.

If the deployment script uses Kubernetes, you can run kubectl commands to inspect pod status, logs, and events.

Check for container/pod abnormalities, including recent restarts, high CPU or memory usage, or other abnormal behavior in their logs."""

        system_prompt = system_prompt.format(
            repo_path=self.repo_path, attempt=attempt, max_attempts=max_attempts
        )

        user_prompt = f"""The deployment has failed. Please analyze the error and fix the deployment scripts.

{error_context}{previous_summary_note}

Please:
1. Analyze what went wrong
2. Identify the root cause
3. Fix the scripts in .sds/deploy.sh and/or .sds/health_check.sh
4. Explain your fix

The scripts are located at:
- {self.deploy_script}
- {self.health_check_script}

You have read/write access to these files. Please fix the issues and help get the deployment working."""

        return f"{system_prompt}\n\n{user_prompt}"
