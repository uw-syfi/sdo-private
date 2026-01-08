import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List, Any

from app_operator.agent_cli.base import CodingAgent
from app_operator.agent_cli.factory import create_agent_from_config
from app_operator.monitoring import MonitoringTask, HealthCheckTask
# Still needed for initial script generation
from app_operator.script_generator import generate_scripts


class AppOperator:
    """Manages automated deployment with coding-agent-assisted error fixing and extensible monitoring.

    This operator:
    1. Checks for or generates deployment scripts
    2. Attempts deployment and uses a coding agent to fix errors
    3. Monitors application health using a configurable set of MonitoringTasks
    """

    def __init__(self, repo_path: str, health_check_interval: int = 30,
                 agent: Optional[CodingAgent] = None):
        """Initialize the application operator.

        Args:
            repo_path: Path to the repository to deploy.
            health_check_interval: Seconds between health checks (default: 30).
            agent: Optional coding agent to use. If None, creates one from config.
        """
        self.repo_path = Path(repo_path).resolve()
        self.health_check_interval = health_check_interval
        self.check_count = 0  # To track health check count for monitoring messages

        # Initialize agent if not provided
        if agent is None:
            try:
                self.agent = create_agent_from_config(str(self.repo_path))
            except RuntimeError as e:
                # Fallback or error if no default agent can be created
                raise RuntimeError(
                    f"Failed to initialize default coding agent: {e}")
        else:
            self.agent = agent

        self.sds_dir = self.repo_path / ".sds"
        self.deploy_script = self.sds_dir / "deploy.sh"
        self.health_check_script = self.sds_dir / "health_check.sh"
        self._shutdown_requested = False
        self._deployed = False

        # Initialize monitoring tasks
        self.monitoring_tasks: List[MonitoringTask] = [HealthCheckTask()]

        # Validate repository path
        if not self.repo_path.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")
        if not self.repo_path.is_dir():
            raise ValueError(
                f"Repository path is not a directory: {repo_path}")

    def run(self) -> int:
        """Main entry point for application operation.

        Returns:
            int: Exit code (0 for success, 1 for failure).
        """
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

        try:
            print(f"\n{'='*70}")
            print(f"  App Operator Mode")
            print(f"  Repository: {self.repo_path}")
            print(f"  Agent: {self.agent.__class__.__name__}")
            print(f"{'='*70}\n")

            # Step 1: Ensure scripts exist
            if not self._ensure_scripts_exist():
                return 1

            # Step 2: Deploy with automatic error fixing
            if not self._deploy_with_fixing():
                print(
                    "\n✗ Failed to deploy application after multiple attempts",
                    file=sys.stderr)
                return 1

            self._deployed = True

            # Step 3: Monitor health and provide analysis
            self._monitor_application()

            return 0

        except Exception as e:
            print(f"\n✗ Unexpected error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return 1
        finally:
            self._cleanup()

    def _ensure_scripts_exist(self) -> bool:
        """Check if deploy.sh and health_check.sh exist, generate if missing.

        Returns:
            bool: True if scripts exist or were generated successfully.
        """
        # Check if both scripts exist
        if self.deploy_script.exists() and self.health_check_script.exists():
            print(f"✓ Found existing scripts in {self.sds_dir}")
            return True

        # Need to generate scripts
        print(f"\n{'='*70}")
        print(f"  Generating Deployment Scripts")
        print(f"{'='*70}\n")

        print(
            f"Scripts not found in {self.sds_dir}, generating with {self.agent.__class__.__name__}...")

        # Use the generate_scripts function from script_generator
        success, message = generate_scripts(str(self.repo_path), self.agent)

        if success:
            print(f"✓ {message}")
            return True
        else:
            print(f"✗ {message}", file=sys.stderr)
            return False

    def _deploy_with_fixing(self, max_attempts: int = 5) -> bool:
        """Attempt deployment with automatic error fixing using a coding agent.

        Args:
            max_attempts: Maximum number of deployment attempts (default: 5).

        Returns:
            bool: True if deployment succeeded.
        """
        print(f"\n{'='*70}")
        print(f"  Deploying Application with Error Fixing")
        print(f"  Max attempts: {max_attempts}")
        print(f"{'='*70}\n")

        for attempt in range(1, max_attempts + 1):
            if self._shutdown_requested:
                print("\nShutdown requested, aborting deployment")
                return False

            print(f"\n--- Deployment Attempt #{attempt} ---\n")

            # Run deployment script
            deploy_result = self._run_deploy_script()

            # Check if deployment succeeded
            if deploy_result["success"]:
                print(f"\n✓ Deployment script succeeded (exit code: 0)")

                # Verify with health check
                health_result = self._run_health_check()

                if health_result["success"]:
                    print(f"✓ Health check passed (exit code: 0)")
                    print(f"\n{'='*70}")
                    print(f"  Deployment Successful!")
                    print(
                        f"  Monitoring health every {self.health_check_interval} seconds")
                    print(f"  Press Ctrl+C to stop")
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

    def _run_deploy_script(self) -> dict:
        """Run the deployment script.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        print(f"Running deployment script: {self.deploy_script}")

        try:
            # Run deploy script with "start" command
            result = subprocess.run(
                [str(self.deploy_script), "start"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
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
                "stderr": "Deployment script timed out after 5 minutes"
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Failed to run deployment script: {e}"
            }

    def _run_health_check(self) -> dict:
        """Run the health check script.

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
                timeout=120  # 2 minute timeout
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
                "stderr": "Health check timed out after 2 minutes"
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
            deploy_result: dict,
            health_result: Optional[dict],
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
        print(f"{'='*70}\n")

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
            response = self.agent.generate(
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
            self, deploy_result: dict, health_result: Optional[dict]) -> str:
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

    def _monitor_application(self):
        """Monitor application health and provide agent analysis every interval."""
        print(
            f"\nStarting application monitoring (interval: {self.health_check_interval}s)...")

        self.check_count = 0  # Reset check count for new monitoring session

        while not self._shutdown_requested:
            # Wait for interval
            for _ in range(self.health_check_interval):
                if self._shutdown_requested:
                    return
                time.sleep(1)

            self.check_count += 1

            print(f"\n{'='*70}")
            print(f"  Monitoring Cycle #{self.check_count}")
            print(f"{'='*70}\n")

            # Run all registered monitoring tasks
            for task in self.monitoring_tasks:
                task.run(self)

    def _analyze_health_with_agent(
            self,
            health_result: dict, check_count: int):
        """Use a coding agent to analyze health check results and provide suggestions.

        Args:
            health_result: Health check result.
            check_count: Current check count.
        """
        print(f"\n{'-'*70}")
        print(
            f"  Asking {self.agent.__class__.__name__} to Analyze Health Check Results")
        print(f"{'-'*70}\n")

        # Prepare health check context
        context = self._prepare_health_context(health_result, check_count)

        # Create analysis prompt
        prompt = self._create_analysis_prompt(context)

        try:
            print(
                f"Consulting {self.agent.__class__.__name__} for health analysis and suggestions...")

            # Run agent to get analysis
            response = self.agent.generate(
                prompt, cwd=str(self.repo_path), timeout=120)

            print(f"\n{'='*70}")
            print(f"  {self.agent.__class__.__name__} Analysis Complete")
            print(f"{'='*70}\n")

            # Note: We're not acting on suggestions yet, just displaying them
            print(
                f"Note: Suggestions are for information only, not automatically applied")

        except Exception as e:
            print(f"✗ Agent analysis failed: {e}", file=sys.stderr)

    def _prepare_health_context(
            self,
            health_result: dict, check_count: int) -> str:
        """Prepare health check context for analysis.

        Args:
            health_result: Health check result.
            check_count: Current check count.

        Returns:
            str: Formatted context.
        """
        context_parts = []

        context_parts.append(f"## Health Check #{check_count}")
        context_parts.append(f"Exit Code: {health_result['exit_code']}")
        context_parts.append(
            f"Status: {'PASSED' if health_result['success'] else 'FAILED'}")
        context_parts.append(
            f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")

        if health_result['stdout']:
            context_parts.append("\n### Output:")
            stdout = health_result['stdout']
            # For health checks, include more output (up to 5000 chars)
            if len(stdout) > 5000:
                stdout = stdout[-5000:]
                context_parts.append(
                    "... (truncated, showing last 5000 chars)")
            context_parts.append(stdout)

        if health_result['stderr']:
            context_parts.append("\n### Errors:")
            stderr = health_result['stderr']
            if len(stderr) > 2000:
                stderr = stderr[-2000:]
                context_parts.append(
                    "... (truncated, showing last 2000 chars)")
            context_parts.append(stderr)

        return "\n".join(context_parts)

    def _create_analysis_prompt(self, context: str) -> str:
        """Create a prompt for the coding agent to analyze health check results.

        Args:
            context: Formatted health check context.

        Returns:
            str: The prompt for the agent.
        """
        system_prompt = """You are an expert SRE (Site Reliability Engineer) analyzing application health metrics.

Your task is to analyze health check results and provide actionable insights and suggestions.

## Context
- Repository: {repo_path}
- Application is currently deployed and running
- You are analyzing periodic health check results
- Your suggestions will NOT be automatically applied

## DO's:
- Carefully analyze all health metrics and indicators
- Identify any issues, warnings, or anomalies
- Provide clear, actionable suggestions for improvement
- Prioritize issues by severity (critical, warning, info)
- Consider performance, reliability, and resource utilization
- Look for trends or patterns if multiple checks have been performed
- Suggest proactive improvements, not just reactive fixes
- Be specific about what to check or fix
- Consider monitoring and observability improvements
- Suggest optimization opportunities

## DON'Ts:
- Don't ignore warnings or minor issues
- Don't provide vague suggestions
- Don't recommend destructive actions without clear warnings
- Don't panic over temporary blips (distinguish from real issues)
- Don't focus only on failures - also suggest improvements for healthy systems
- Don't make assumptions without evidence from the health check output

## Analysis Framework:
1. Overall Health Status
   - Is the system healthy?
   - Are all components functioning?
   - Any degraded services?

2. Issues Found
   - Critical issues (requires immediate attention)
   - Warnings (should be addressed soon)
   - Info (nice to have improvements)

3. Performance Analysis
   - Response times
   - Resource utilization
   - Bottlenecks

4. Recommendations
   - Short-term fixes
   - Long-term improvements
   - Monitoring suggestions

## Output Format:
Provide a structured analysis with:
- Executive summary (1-2 sentences)
- Detailed findings
- Prioritized recommendations
- Suggested actions (if any)

Keep your analysis concise but comprehensive."""

        system_prompt = system_prompt.format(repo_path=self.repo_path)

        user_prompt = f"""Please analyze the following health check results and provide insights and suggestions:

{context}

Provide:
1. Overall health assessment
2. Any issues or concerns
3. Performance observations
4. Recommendations for improvement
5. Suggested actions (if any)

Remember: Your suggestions are for information only and will not be automatically applied."""

        return f"{system_prompt}\n\n{user_prompt}"

    def _handle_shutdown_signal(self, signum: int, frame):
        """Handle shutdown signals (SIGINT, SIGTERM).

        Args:
            signum: The signal number.
            frame: The current stack frame.
        """
        if not self._shutdown_requested:
            self._shutdown_requested = True
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            print(
                f"\n\nReceived {signal_name} signal. Initiating graceful shutdown...")

    def _cleanup(self):
        """Shutdown the application and cleanup resources."""
        if not self._deployed:
            return

        print(f"\n{'='*70}")
        print(f"  Shutting Down Application")
        print(f"{'='*70}\n")

        print(f"Running deployment script stop command...")

        try:
            result = subprocess.run(
                [str(self.deploy_script), "stop"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)

            if result.returncode == 0:
                print(f"✓ Application stopped successfully")
            else:
                print(
                    f"⚠ Stop command exited with code {result.returncode}",
                    file=sys.stderr)
        except Exception as e:
            print(f"✗ Error during shutdown: {e}", file=sys.stderr)

        print(f"\n{'='*70}")
        print(f"  Shutdown Complete")
        print(f"{'='*70}\n")
