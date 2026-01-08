"""Script generator using coding agents to create deploy and health check scripts."""

import os
import shutil
import subprocess
import sys
import threading
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class CodingAgent(ABC):
    """Abstract base class for coding agents."""

    @abstractmethod
    def generate(self, prompt: str,
                 cwd: Optional[str] = None, timeout: int = 300) -> str:
        """Generate text/code based on a prompt.

        Args:
            prompt: The prompt to send to the agent.
            cwd: Optional working directory context.
            timeout: Timeout in seconds.

        Returns:
            Generated text.
        """
        pass


class CodexCodingAgent(CodingAgent):
    """Coding agent implementation using the Codex CLI tool."""

    def __init__(self, model: Optional[str] = None):
        """Initialize the Codex coding agent.

        Args:
            model: Optional model name to use with codex. If None, uses default.

        Raises:
            RuntimeError: If codex binary is not found in PATH.
        """
        codex_path = shutil.which("codex")
        if not codex_path:
            raise RuntimeError(
                "codex binary not found in PATH. "
                "Please ensure codex is installed and available."
            )
        self.codex_path = codex_path
        self.model = model

    def generate(self, prompt: str,
                 cwd: Optional[str] = None, timeout: int = 300) -> str:
        """Generate text using codex.

        Args:
            prompt: The prompt to send to codex.
            cwd: Optional working directory to run codex in. If None, uses current directory.
            timeout: Timeout in seconds (default: 300).

        Returns:
            Generated text from codex.

        Raises:
            RuntimeError: If codex execution fails.
            subprocess.TimeoutExpired: If codex times out.
        """
        # Prepare codex command
        # "exec" to run in non-interactive mode
        cmd = [
            self.codex_path,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox"
        ]
        if self.model:
            cmd.extend(["--model", self.model])

        print(f"[CodexCodingAgent] Running command: {' '.join(cmd)}")
        print(f"[CodexCodingAgent] Working directory: {cwd or os.getcwd()}")
        print(f"[CodexCodingAgent] Prompt length: {len(prompt)} characters")
        print("-" * 80)
        sys.stdout.flush()

        # Buffers to capture output
        stdout_lines = []
        stderr_lines = []

        def read_stdout(pipe, buffer):
            """Read stdout line by line and print + capture."""
            for line in iter(pipe.readline, ''):
                if not line:
                    break
                line_stripped = line.rstrip('\n')
                print(f"[CodexCodingAgent] {line_stripped}")
                sys.stdout.flush()
                buffer.append(line)
            pipe.close()

        def read_stderr(pipe, buffer):
            """Read stderr line by line and print + capture."""
            for line in iter(pipe.readline, ''):
                if not line:
                    break
                line_stripped = line.rstrip('\n')
                print(
                    f"[CodexCodingAgent] [STDERR] {line_stripped}",
                    file=sys.stderr)
                sys.stderr.flush()
                buffer.append(line)
            pipe.close()

        # Run codex with Popen to capture and print output in real-time
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            cwd=cwd
        )

        # Start threads to read stdout and stderr concurrently
        stdout_thread = threading.Thread(
            target=read_stdout, args=(
                process.stdout, stdout_lines))
        stderr_thread = threading.Thread(
            target=read_stderr, args=(
                process.stderr, stderr_lines))

        stdout_thread.daemon = True
        stderr_thread.daemon = True

        stdout_thread.start()
        stderr_thread.start()

        # Send the prompt to stdin and close it
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except BrokenPipeError:
            pass

        # Wait for process to complete with timeout
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise subprocess.TimeoutExpired(cmd, timeout)

        # Wait for threads to finish reading
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        # Combine captured output
        stdout_data = ''.join(stdout_lines)
        stderr_data = ''.join(stderr_lines)

        print("-" * 80)

        if process.returncode != 0:
            raise RuntimeError(
                f"codex exited with code {process.returncode}: {stderr_data}"
            )

        print(f"[CodexCodingAgent] Command completed successfully (exit code: 0)")
        print("=" * 80)
        sys.stdout.flush()

        return stdout_data.strip()


class GeminiCodingAgent(CodingAgent):
    """Coding agent implementation using the Gemini CLI tool."""

    def __init__(self, model: Optional[str] = None):
        """Initialize the Gemini coding agent.

        Args:
            model: Optional model name to use.

        Raises:
            RuntimeError: If gemini binary is not found in PATH.
        """
        gemini_path = shutil.which("gemini")
        if not gemini_path:
            raise RuntimeError(
                "gemini binary not found in PATH. "
                "Please ensure gemini is installed and available."
            )
        self.gemini_path = gemini_path
        self.model = model

    def generate(self, prompt: str,
                 cwd: Optional[str] = None, timeout: int = 300) -> str:
        """Generate text using gemini.

        Args:
            prompt: The prompt to send to gemini.
            cwd: Optional working directory to run gemini in.
            timeout: Timeout in seconds (default: 300).

        Returns:
            Generated text from gemini.
        """
        # Prepare gemini command
        cmd = [self.gemini_path]
        if self.model:
            cmd.extend(["--model", self.model])

        print(f"[GeminiCodingAgent] Running command: {' '.join(cmd)}")
        print(f"[GeminiCodingAgent] Working directory: {cwd or os.getcwd()}")
        print(f"[GeminiCodingAgent] Prompt length: {len(prompt)} characters")
        print("-" * 80)
        sys.stdout.flush()

        # Buffers to capture output
        stdout_lines = []
        stderr_lines = []

        def read_stdout(pipe, buffer):
            """Read stdout line by line and print + capture."""
            for line in iter(pipe.readline, ''):
                if not line:
                    break
                line_stripped = line.rstrip('\n')
                print(f"[GeminiCodingAgent] {line_stripped}")
                sys.stdout.flush()
                buffer.append(line)
            pipe.close()

        def read_stderr(pipe, buffer):
            """Read stderr line by line and print + capture."""
            for line in iter(pipe.readline, ''):
                if not line:
                    break
                line_stripped = line.rstrip('\n')
                print(
                    f"[GeminiCodingAgent] [STDERR] {line_stripped}",
                    file=sys.stderr)
                sys.stderr.flush()
                buffer.append(line)
            pipe.close()

        # Run gemini with Popen to capture and print output in real-time
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            cwd=cwd
        )

        # Start threads to read stdout and stderr concurrently
        stdout_thread = threading.Thread(
            target=read_stdout, args=(
                process.stdout, stdout_lines))
        stderr_thread = threading.Thread(
            target=read_stderr, args=(
                process.stderr, stderr_lines))

        stdout_thread.daemon = True
        stderr_thread.daemon = True

        stdout_thread.start()
        stderr_thread.start()

        # Send the prompt to stdin and close it
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except BrokenPipeError:
            pass

        # Wait for process to complete with timeout
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise subprocess.TimeoutExpired(cmd, timeout)

        # Wait for threads to finish reading
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        # Combine captured output
        stdout_data = ''.join(stdout_lines)
        stderr_data = ''.join(stderr_lines)

        print("-" * 80)

        if process.returncode != 0:
            raise RuntimeError(
                f"gemini exited with code {process.returncode}: {stderr_data}"
            )

        print(f"[GeminiCodingAgent] Command completed successfully (exit code: 0)")
        print("=" * 80)
        sys.stdout.flush()

        return stdout_data.strip()


# For backward compatibility
CodexLLM = CodexCodingAgent


def create_agent_from_config(
        target_dir: str, model_override: Optional[str] = None) -> CodingAgent:
    """Create a coding agent based on configuration file in target directory.

    Looks for sds.toml or config.toml in the target directory.
    Default to CodexCodingAgent if no config found or provider is not specified.

    Args:
        target_dir: Directory to look for configuration files.
        model_override: Optional model name to override config.

    Returns:
        CodingAgent: Configured coding agent.
    """
    target_path = Path(target_dir)
    config_files = [target_path / "sds.toml", target_path / "config.toml"]

    provider = "codex"
    model = model_override

    for config_file in config_files:
        if config_file.exists():
            try:
                with open(config_file, "rb") as f:
                    config = tomllib.load(f)
                    agent_config = config.get("agent", {})
                    if "provider" in agent_config:
                        provider = agent_config["provider"]
                    if not model and "model" in agent_config:
                        model = agent_config["model"]
                print(f"Loaded configuration from {config_file}")
                break
            except Exception as e:
                print(
                    f"Warning: Failed to parse {config_file}: {e}",
                    file=sys.stderr)

    if provider.lower() == "gemini":
        return GeminiCodingAgent(model=model)
    else:
        return CodexCodingAgent(model=model)


def generate_scripts(
        target_dir: str, agent: Optional[CodingAgent] = None) -> tuple[bool, str]:
    """Generate deploy.sh and health_check.sh scripts using a coding agent.

    This function runs the coding agent in the target directory with read/write access,
    analyzing the repository structure and generating appropriate deployment
    and health check scripts.

    Args:
        target_dir: The directory path where scripts should be generated.
        agent: Optional CodingAgent instance. If None, creates one from config.

    Returns:
        Tuple of (success: bool, message: str).
    """
    target_path = Path(target_dir).resolve()

    # Validate target directory exists
    if not target_path.exists():
        return False, f"Target directory does not exist: {target_dir}"

    if not target_path.is_dir():
        return False, f"Target path is not a directory: {target_dir}"

    # Initialize coding agent if not provided
    if agent is None:
        try:
            agent = create_agent_from_config(str(target_path))
        except RuntimeError as e:
            return False, str(e)

    # Create .sds directory if it doesn't exist
    sds_dir = target_path / ".sds"
    sds_dir.mkdir(exist_ok=True)

    # Get absolute path for context
    abs_target_dir = str(target_path)

    # Change to target directory for context
    original_cwd = os.getcwd()
    try:
        os.chdir(abs_target_dir)

        # Create system prompt
        system_prompt = _create_system_prompt()

        # Analyze the repository structure
        repo_context = _analyze_repository(target_path)

        # Generate deploy.sh using coding agent
        deploy_success, deploy_content = _generate_deploy_script(
            agent, system_prompt, repo_context, abs_target_dir
        )

        if not deploy_success:
            return False, f"Failed to generate deploy.sh: {deploy_content}"

        # Generate health_check.sh using coding agent
        health_check_success, health_check_content = _generate_health_check_script(
            agent, system_prompt, repo_context, abs_target_dir
        )

        if not health_check_success:
            return False, f"Failed to generate health_check.sh: {health_check_content}"

        # Write scripts to .sds directory
        deploy_script_path = sds_dir / "deploy.sh"
        health_check_script_path = sds_dir / "health_check.sh"

        deploy_script_path.write_text(deploy_content, encoding="utf-8")
        deploy_script_path.chmod(0o755)  # Make executable

        health_check_script_path.write_text(
            health_check_content, encoding="utf-8")
        health_check_script_path.chmod(0o755)  # Make executable

        return True, f"Successfully generated scripts in {sds_dir}"

    except Exception as e:
        return False, f"Failed to generate scripts: {e}"
    finally:
        # Restore original working directory
        os.chdir(original_cwd)


def _create_system_prompt() -> str:
    """Create the system prompt for coding agent to guide script generation."""
    return """You are an expert DevOps engineer generating deployment and health check scripts for applications.

Your task is to analyze a repository and generate two bash scripts:
1. deploy.sh - A comprehensive deployment script
2. health_check.sh - A comprehensive health check script

## DO's:
- Use proper bash scripting practices (set -e, proper error handling)
- Include color-coded output (RED, GREEN, YELLOW, BLUE, CYAN, MAGENTA, NC)
- Add helper functions for printing (print_header, print_success, print_error, print_warning, print_info)
- Check prerequisites (Docker, Docker Compose, required tools)
- Use SCRIPT_DIR and proper path resolution
- Include comprehensive error handling
- Add command-line argument parsing
- Include help/usage information
- Make scripts executable-ready (shebang #!/bin/bash)
- For deploy.sh: Include start, stop, restart, status, logs, build, cleanup commands
- For health_check.sh: Check containers, ports, endpoints, databases, performance metrics
- Include summary reports and exit codes
- Use docker compose commands if docker-compose.yml exists
- Check for Kubernetes manifests if k8s/ directory exists
- Adapt to the specific application type (microservices, monolith, etc.)

## DON'Ts:
- Don't hardcode absolute paths (use SCRIPT_DIR and relative paths)
- Don't skip error handling
- Don't use deprecated commands
- Don't generate overly complex scripts - keep them maintainable
- Don't assume specific port numbers without checking the codebase
- Don't include hardcoded credentials or secrets
- Don't generate scripts that modify files outside the application directory
- Don't skip prerequisite checks

## Example Structure (DO NOT COPY EXACTLY - ADAPT TO REPOSITORY):

deploy.sh should include:
- Configuration section with paths and environment variables
- Helper functions for colored output
- check_prerequisites() function
- build_images() function (if using Docker)
- start_services() function
- stop_services() function
- restart_services() function
- check_health() function
- view_logs() function
- cleanup() function
- Main command dispatcher with case statement

health_check.sh should include:
- Configuration section
- Helper functions for colored output and status tracking
- check_docker() function
- check_containers() function
- check_ports() function
- check_endpoints() function (HTTP/HTTPS health checks)
- check_databases() function (if applicable)
- check_cache() function (if applicable)
- check_performance() function
- print_summary() function with health score
- Main execution flow

## Important:
- Analyze the repository structure to understand the deployment method
- Look for docker-compose.yml, Dockerfile, Kubernetes manifests, Makefile, etc.
- Identify the main application entry points and services
- Determine health check endpoints and ports
- Adapt the scripts to match the actual application architecture
- Use relative paths based on SCRIPT_DIR
- Make the scripts robust and production-ready"""


def _analyze_repository(repo_path: Path) -> str:
    """Analyze repository structure and return context string."""
    context_parts = []

    # Check for common deployment files
    if (repo_path / "docker-compose.yml").exists():
        context_parts.append(
            "- Found docker-compose.yml (Docker Compose deployment)")
    if (repo_path / "docker-compose.yaml").exists():
        context_parts.append(
            "- Found docker-compose.yaml (Docker Compose deployment)")
    if (repo_path / "Dockerfile").exists():
        context_parts.append("- Found Dockerfile (Docker-based application)")
    if (repo_path / "k8s").exists() or (repo_path / "kubernetes").exists():
        context_parts.append("- Found Kubernetes manifests directory")
    if (repo_path / "Makefile").exists():
        context_parts.append(
            "- Found Makefile (may contain build/deploy targets)")

    # Check for common application files
    if (repo_path / "package.json").exists():
        context_parts.append("- Found package.json (Node.js application)")
    if (repo_path / "requirements.txt").exists() or (repo_path /
                                                     "pyproject.toml").exists():
        context_parts.append(
            "- Found Python dependencies (Python application)")
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
            f"- Found README file(s): {', '.join(f.name for f in readme_files)}")

    # List top-level directories
    dirs = [d for d in repo_path.iterdir() if d.is_dir()
            and not d.name.startswith('.')]
    if dirs:
        dir_names = ', '.join(
            sorted([d.name for d in dirs[:10]]))  # Limit to 10
        context_parts.append(f"- Top-level directories: {dir_names}")

    # Get repository name
    repo_name = repo_path.name
    context_parts.insert(0, f"Repository: {repo_name}")

    return "\n".join(
        context_parts) if context_parts else "Repository structure analysis: No obvious deployment files found"


def _generate_deploy_script(
    agent: CodingAgent,
    system_prompt: str,
    repo_context: str,
    target_dir: str
) -> tuple[bool, str]:
    """Generate deploy.sh script using a coding agent."""
    human_prompt = f"""Generate a comprehensive deploy.sh bash script for the following repository:

{repo_context}

Target directory: {target_dir}

Requirements:
- The script should be placed in a .sds subdirectory
- Use SCRIPT_DIR to determine paths relative to the script location
- Analyze the repository structure to determine the deployment method
- Include all standard deployment commands (start, stop, restart, status, logs, build, cleanup)
- Make it robust, well-documented, and production-ready
- Include proper error handling and colored output
- The script should work when executed from the .sds directory

Generate ONLY the bash script content, starting with #!/bin/bash. Do not include any markdown formatting or code fences."""

    full_prompt = f"""{system_prompt}

{human_prompt}"""

    try:
        script_content = agent.generate(full_prompt, cwd=target_dir)

        # Remove markdown code fences if present
        if script_content.startswith("```bash"):
            script_content = script_content[7:]
        elif script_content.startswith("```"):
            script_content = script_content[3:]
        if script_content.endswith("```"):
            script_content = script_content[:-3]

        script_content = script_content.strip()

        # Ensure it starts with shebang
        if not script_content.startswith("#!/bin/bash"):
            script_content = "#!/bin/bash\n\n" + script_content

        return True, script_content
    except subprocess.TimeoutExpired:
        return False, "agent command timed out after 5 minutes"
    except Exception as e:
        return False, str(e)


def _generate_health_check_script(
    agent: CodingAgent,
    system_prompt: str,
    repo_context: str,
    target_dir: str
) -> tuple[bool, str]:
    """Generate health_check.sh script using a coding agent."""
    human_prompt = f"""Generate a comprehensive health_check.sh bash script for the following repository:

{repo_context}

Target directory: {target_dir}

Requirements:
- The script should be placed in a .sds subdirectory
- Use SCRIPT_DIR to determine paths relative to the script location
- Analyze the repository structure to determine what to check
- Include checks for: containers, ports, endpoints, databases, cache, performance
- Include a summary report with health score
- Make it robust, well-documented, and production-ready
- Include proper error handling and colored output
- Track total checks, passed checks, failed checks, warnings
- The script should work when executed from the .sds directory

Generate ONLY the bash script content, starting with #!/bin/bash. Do not include any markdown formatting or code fences."""

    full_prompt = f"""{system_prompt}

{human_prompt}"""

    try:
        script_content = agent.generate(full_prompt, cwd=target_dir)

        # Remove markdown code fences if present
        if script_content.startswith("```bash"):
            script_content = script_content[7:]
        elif script_content.startswith("```"):
            script_content = script_content[3:]
        if script_content.endswith("```"):
            script_content = script_content[:-3]

        script_content = script_content.strip()

        # Ensure it starts with shebang
        if not script_content.startswith("#!/bin/bash"):
            script_content = "#!/bin/bash\n\n" + script_content

        return True, script_content
    except subprocess.TimeoutExpired:
        return False, "agent command timed out after 5 minutes"
    except Exception as e:
        return False, str(e)
