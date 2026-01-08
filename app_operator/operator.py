import signal
import sys
import time
from pathlib import Path
from typing import Optional, List, Any

from app_operator.agent_cli.base import CodingAgent
from app_operator.agent_cli.factory import create_agent_from_config
from app_operator.monitoring import MonitoringTask, HealthCheckTask
from app_operator.agents.deployer import DeploymentAgent


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
        self._shutdown_requested = False
        self._deployed = False

        # Initialize deployment agent
        self.deployer = DeploymentAgent(self.repo_path, self.agent)

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

            # Step 1: Deploy with automatic error fixing (includes script
            # generation)
            if not self.deployer.run(
                    check_shutdown=lambda: self._shutdown_requested):
                print(
                    "\n✗ Failed to deploy application after multiple attempts",
                    file=sys.stderr)
                return 1

            self._deployed = True

            # Step 2: Monitor health and provide analysis
            self._monitor_application()

            return 0

        except Exception as e:
            print(f"\n✗ Unexpected error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return 1
        finally:
            self._cleanup()

    def _run_health_check(self) -> dict:
        """Run the health check script.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        # Proxy to deployer
        return self.deployer.run_health_check()

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
            # Use deployer to stop
            result = self.deployer.run_deploy_command("stop", timeout=120)

            if result['success']:
                print(f"✓ Application stopped successfully")
            else:
                print(
                    f"⚠ Stop command exited with code {result['exit_code']}",
                    file=sys.stderr)
        except Exception as e:
            print(f"✗ Error during shutdown: {e}", file=sys.stderr)

        print(f"\n{'='*70}")
        print(f"  Shutdown Complete")
        print(f"{'='*70}\n")
