import sys
import time
import re
import shutil
import contextlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List

from app_operator.agent_cli.base import CodingAgent
from tools.healthcheck import run_health_check


class MonitoringTask(ABC):
    """Abstract base class for monitoring tasks."""

    @abstractmethod
    def run(self, operator: Any) -> None:
        """Execute the monitoring task.

        Args:
            operator: The AppOperator/AppMonitor instance running this task.
        """
        pass

    @abstractmethod
    def analyze(self, agent: CodingAgent, context: str) -> None:
        """Use a coding agent to analyze results and provide suggestions."""
        pass


class HealthCheckTask(MonitoringTask):
    """A monitoring task specifically for running health checks."""

    def run(self, monitor: Any) -> None:
        """Run the health check task.

        Args:
            monitor: The AppMonitor instance.
        """
        health_result = run_health_check(
            monitor.repo_path, monitor.health_check_script)
        self.analyze(monitor, health_result)

    def analyze(self, monitor: Any, health_result: Dict[str, Any]) -> None:
        """Analyze health check results using the agent."""
        print(f"\n{'-'*70}")
        print(
            f"  Asking {monitor.agent.__class__.__name__} to Analyze Health Check Results")
        print(f"{'-'*70}\n")

        # Prepare health check context
        context = self._prepare_health_context(
            health_result, monitor.check_count)

        # Create analysis prompt
        prompt = self._create_analysis_prompt(context, monitor.repo_path)

        try:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            # Ensure log directory exists
            monitor.log_dir.mkdir(parents=True, exist_ok=True)
            log_file = monitor.log_dir / f"check_{monitor.check_count}_{timestamp}.log"

            print(
                f"Consulting {monitor.agent.__class__.__name__} for health analysis...")

            # Run agent and redirect its output to the log file
            with open(log_file, "w") as f:
                with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
                    response = monitor.agent.generate(
                        prompt, cwd=str(monitor.repo_path), timeout=120)

                # Explicitly write the response to the log file
                f.write("\n\n=== Agent Analysis ===\n")
                f.write(response)

            # Extract executive summary
            match = re.search(r"<exec_summary>(.*?)</exec_summary>", response, re.DOTALL)
            if match:
                summary = match.group(1).strip()
                print(f"\nSummary: {summary}")
            else:
                print("\nSummary not found in expected XML format. See log for full analysis.")

            print(f"\nFull analysis saved to: {log_file}")

        except Exception as e:
            print(f"✗ Agent analysis failed: {e}", file=sys.stderr)

    def _prepare_health_context(
            self,
            health_result: dict, check_count: int) -> str:
        """Prepare health check context for analysis."""
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

    def _create_analysis_prompt(self, context: str, repo_path: Path) -> str:
        """Create a prompt for the coding agent to analyze health check results."""
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
- Executive summary wrapped in <exec_summary> tags.
  * THE EXECUTIVE SUMMARY MUST BE NO LONGER THAN 2 LINES.
- Detailed findings
- Prioritized recommendations
- Suggested actions (if any)

Keep your analysis concise but comprehensive. Wrap the executive summary like this: <exec_summary>Your summary here</exec_summary>. Everything else should be outside these tags."""

        system_prompt = system_prompt.format(repo_path=repo_path)

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


class AppMonitor:
    """Agent responsible for monitoring application health."""

    def __init__(self, repo_path: Path, agent: CodingAgent):
        """Initialize the monitor agent.

        Args:
            repo_path: Path to the repository.
            agent: The coding agent to use for analysis.
        """
        self.repo_path = repo_path
        self.agent = agent
        self.monitoring_tasks: List[MonitoringTask] = [HealthCheckTask()]
        self.check_count = 0
        self.health_check_script = self.repo_path / ".sds" / "health_check.sh"
        self.log_dir = self.repo_path / ".sds" / "logs" / "monitor"

    def run(self, interval: int = 30,
            check_shutdown: Optional[Callable[[], bool]] = None):
        """Monitor application health and provide agent analysis every interval.

        Args:
            interval: Seconds between checks.
            check_shutdown: Callable returning True if shutdown requested.
        """
        print(
            f"\nStarting application monitoring (interval: {interval}s)...")

        # Clear log directory on startup
        if self.log_dir.exists():
            shutil.rmtree(self.log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.check_count = 0

        while not (check_shutdown and check_shutdown()):
            # Wait for interval
            for _ in range(interval):
                if check_shutdown and check_shutdown():
                    return
                time.sleep(1)

            self.check_count += 1

            print(f"\n{'='*70}")
            print(f"  Monitoring Cycle #{self.check_count}")
            print(f"{'='*70}\n")

            # Run all registered monitoring tasks
            for task in self.monitoring_tasks:
                task.run(self)
