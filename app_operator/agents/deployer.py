import sys
import subprocess
import time
from pathlib import Path
from typing import Optional, Callable, Dict, Any

from app_operator.agent_cli.base import CodingAgent
from app_operator.script_generator import generate_scripts


class DeploymentAgent:
    """Agent responsible for deploying applications and fixing deployment errors."""

    def __init__(self, repo_path: Path, coding_agent: CodingAgent):
        """Initialize the deployment agent.

        Args:
            repo_path: Path to the repository to deploy.
            coding_agent: The coding agent to use for generating/fixing scripts.
        """
        self.repo_path = repo_path
        self.agent = coding_agent
        self.sds_dir = self.repo_path / ".sds"
        self.deploy_script = self.sds_dir / "deploy.sh"
        self.health_check_script = self.sds_dir / "health_check.sh"

    def run(self, max_attempts: int = 5,
            check_shutdown: Optional[Callable[[], bool]] = None) -> bool:
        """Attempt deployment with automatic error fixing using a coding agent.
        Ensures scripts exist before deployment.

        Args:
            max_attempts: Maximum number of deployment attempts (default: 5).
            check_shutdown: Optional callable that returns True if shutdown is requested.

        Returns:
            bool: True if deployment succeeded.
        """
        # Step 1: Ensure scripts exist
        if not (self.deploy_script.exists()
                and self.health_check_script.exists()):
            print(f"\n{'='*70}")
            print(f"  Generating Deployment Scripts")
            print(f"{'='*70}\n")
            print(
                f"Scripts not found in {self.sds_dir}, generating with {self.agent.__class__.__name__}...")

            success, message = generate_scripts(
                str(self.repo_path), self.agent)

            if success:
                print(f"✓ {message}")
            else:
                print(f"✗ {message}", file=sys.stderr)
                return False
        else:
            print(f"✓ Found existing scripts in {self.sds_dir}")

        # Step 2: Deploy with fixing
        print(f"\n{'='*70}")
        print(f"  Deploying Application with Error Fixing")
        print(f"  Max attempts: {max_attempts}")
        print(f"{'='*70}\n")

        for attempt in range(1, max_attempts + 1):
            if check_shutdown and check_shutdown():
                print("\nShutdown requested, aborting deployment")
                return False

            print(f"\n--- Deployment Attempt #{attempt} ---\n")

            # Run deployment script
            deploy_result = self.run_deploy_command("start")

            # Check if deployment succeeded
            if deploy_result["success"]:
                print(f"\n✓ Deployment script succeeded (exit code: 0)")

                # Verify with health check
                health_result = self.run_health_check()

                if health_result["success"]:
                    print(f"✓ Health check passed (exit code: 0)")
                    print(f"\n{'='*70}")
                    print(f"  Deployment Successful!")
                    print(f"{'='*70}\n")
                    return True
                else:
                    print(
                        f"⚠ Health check failed (exit code: {health_result['exit_code']})")

                    # Health check failed - ask agent to analyze and fix
                    if not self._fix_with_agent(
                            deploy_result, health_result, attempt, max_attempts):
                        continue
            else:
                print(
                    f"✗ Deployment script failed (exit code: {deploy_result['exit_code']})")

                # Deployment failed - ask agent to analyze and fix
                if not self._fix_with_agent(
                        deploy_result, None, attempt, max_attempts):
                    continue

        return False

    def run_deploy_command(self, command: str = "start",
                           timeout: int = 300) -> Dict[str, Any]:
        """Run the deployment script with a specific command.

        Args:
            command: The command to pass to the script (e.g., "start", "stop").
            timeout: Timeout in seconds.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        print(f"Running deployment script: {self.deploy_script} {command}")

        try:
            # Run deploy script with command
            result = subprocess.run(
                [str(self.deploy_script), command],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=timeout
            )

            # Print output
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Deployment script timed out after {timeout} seconds"
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Failed to run deployment script: {e}"
            }

    def run_health_check(self, timeout: int = 120) -> Dict[str, Any]:
        """Run the health check script.

        Args:
            timeout: Timeout in seconds.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        print(f"Running health check: {self.health_check_script}")

        try:
            result = subprocess.run(
                [str(self.health_check_script)],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=timeout
            )

            # Print output
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Health check timed out after {timeout} seconds"
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Failed to run health check: {e}"
            }

    def _fix_with_agent(
            self,
            deploy_result: Dict[str, Any],
            health_result: Optional[Dict[str, Any]],
            attempt: int,
            max_attempts: int) -> bool:
        """Use a coding agent to analyze errors and fix the scripts.

        Args:
            deploy_result: Deployment script result.
            health_result: Health check result (None if deployment failed before health check).
            attempt: Current attempt number.
            max_attempts: Maximum number of attempts.

        Returns:
            bool: True if agent suggested a fix and applied it.
        """
        if attempt >= max_attempts:
            print(f"\n✗ Reached maximum attempts ({max_attempts}), giving up")
            return False

        print(f"\n{'='*70}")
        print(
            f"  Asking {self.agent.__class__.__name__} to Fix Deployment Issues")
        print(f"{ '='*70}\n")

        # Prepare error context
        error_context = self._prepare_error_context(
            deploy_result, health_result)

        # Create fix prompt
        prompt = self._create_fix_prompt(error_context, attempt, max_attempts)

        try:
            print(
                f"Consulting {self.agent.__class__.__name__} to analyze and fix the issue...")
            print(f"{'-'*70}")

            # Run agent to get fix suggestions
            # Note: The agent is expected to modify files directly
            self.agent.generate(
                prompt, cwd=str(self.repo_path), timeout=300)

            print(f"{'-'*70}")
            print(f"\nAgent response received")

            # Agent should have modified the scripts directly
            # Just notify user and continue to next attempt
            print(f"\n✓ Agent has analyzed the issue and may have modified the scripts")
            print(f"  Proceeding to next deployment attempt...\n")

            return True

        except Exception as e:
            print(f"✗ Agent failed to provide fix: {e}", file=sys.stderr)
            return False

    def _prepare_error_context(
            self, deploy_result: Dict[str, Any], health_result: Optional[Dict[str, Any]]) -> str:
        """Prepare error context for the coding agent.

        Args:
            deploy_result: Deployment script result.
            health_result: Health check result (None if deployment failed).

        Returns:
            str: Formatted error context.
        """
        context_parts = []

        # Deployment result
        context_parts.append("## Deployment Script Result")
        context_parts.append(f"Exit Code: {deploy_result['exit_code']}")
        context_parts.append(
            f"Status: {'SUCCESS' if deploy_result['success'] else 'FAILED'}")

        if deploy_result['stdout']:
            context_parts.append("\n### STDOUT:")
            # Truncate if too long
            stdout = deploy_result['stdout']
            if len(stdout) > 3000:
                stdout = stdout[-3000:]
                context_parts.append(
                    "... (truncated, showing last 3000 chars)")
            context_parts.append(stdout)

        if deploy_result['stderr']:
            context_parts.append("\n### STDERR:")
            stderr = deploy_result['stderr']
            if len(stderr) > 3000:
                stderr = stderr[-3000:]
                context_parts.append(
                    "... (truncated, showing last 3000 chars)")
            context_parts.append(stderr)

        # Health check result (if available)
        if health_result:
            context_parts.append("\n## Health Check Result")
            context_parts.append(f"Exit Code: {health_result['exit_code']}")
            context_parts.append(
                f"Status: {'SUCCESS' if health_result['success'] else 'FAILED'}")

            if health_result['stdout']:
                context_parts.append("\n### STDOUT:")
                stdout = health_result['stdout']
                if len(stdout) > 3000:
                    stdout = stdout[-3000:]
                    context_parts.append(
                        "... (truncated, showing last 3000 chars)")
                context_parts.append(stdout)

            if health_result['stderr']:
                context_parts.append("\n### STDERR:")
                stderr = health_result['stderr']
                if len(stderr) > 3000:
                    stderr = stderr[-3000:]
                    context_parts.append(
                        "... (truncated, showing last 3000 chars)")
                context_parts.append(stderr)

        return "\n".join(context_parts)

    def _create_fix_prompt(self, error_context: str,
                           attempt: int, max_attempts: int) -> str:
        """Create a prompt for the coding agent to fix deployment errors.

        Args:
            error_context: Formatted error context.
            attempt: Current attempt number.
            max_attempts: Maximum number of attempts.

        Returns:
            str: The prompt for the agent.
        """
        system_prompt = """You are an expert DevOps engineer debugging deployment issues.

Your task is to analyze deployment errors and fix the deployment scripts.

## Context
- Repository: {repo_path}
- Deployment script: .sds/deploy.sh
- Health check script: .sds/health_check.sh
- Current attempt: {attempt} of {max_attempts}

## DO's:
- Carefully analyze the error messages and logs
- Identify the root cause of the deployment failure
- Fix the scripts directly using file editing tools
- Consider common issues: missing dependencies, wrong ports, incorrect paths, permission issues
- Test your understanding of the error before making changes
- Make minimal, targeted fixes
- Ensure scripts are still robust after fixes
- Fix both deployment and health check scripts if needed
- Consider environment-specific issues (Docker, networking, file permissions)
- Look for typos, syntax errors, or incorrect commands
- Check if services are starting in the right order
- Verify port bindings and network configurations
- Check for missing environment variables or configuration files

## DON'Ts:
- Don't make random changes without understanding the error
- Don't remove error handling or safety checks
- Don't introduce new bugs while fixing old ones
- Don't skip analyzing the full error context
- Don't make overly complex changes
- Don't modify files outside .sds directory
- Don't give up easily - try multiple approaches if needed
- Don't assume - verify your assumptions against the error logs

## Approach:
1. Read and analyze the error messages carefully
2. Identify the specific failure point
3. Determine the root cause
4. Read the relevant scripts to understand current implementation
5. Make targeted fixes to address the root cause
6. Verify the fix makes sense in context

## Expected Output:
- Analyze the error thoroughly
- Explain what went wrong
- Describe the fix you're applying
- Make the necessary changes to the scripts
- Confirm the changes are complete

Remember: The goal is to get the application deployed successfully. Be methodical and thorough."""

        system_prompt = system_prompt.format(
            repo_path=self.repo_path,
            attempt=attempt,
            max_attempts=max_attempts
        )

        user_prompt = f"""The deployment has failed. Please analyze the error and fix the deployment scripts.

{error_context}

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
