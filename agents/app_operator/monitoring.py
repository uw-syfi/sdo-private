from abc import ABC, abstractmethod
import sys
import time
from typing import Dict, Any

from app_operator.agent_cli.base import CodingAgent


class MonitoringTask(ABC):
    """Abstract base class for monitoring tasks."""

    @abstractmethod
    def run(self, operator: Any) -> None:
        """Execute the monitoring task.

        Args:
            operator: The AppOperator instance running this task.
        """
        pass

    @abstractmethod
    def analyze(self, agent: CodingAgent, context: str) -> None:
        """Use a coding agent to analyze results and provide suggestions."""
        pass


class HealthCheckTask(MonitoringTask):
    """A monitoring task specifically for running health checks."""

    def run(self, operator: Any) -> None:
        """Run the health check task."""
        health_result = operator._run_health_check()
        operator._analyze_health_with_agent(
            health_result, operator.check_count)

    def analyze(self, agent: CodingAgent, context: str) -> None:
        """Analyze health check results using the agent."""
        # This logic is currently embedded in _analyze_health_with_agent
        # For a truly separate task, this would likely take the context and agent
        # and perform the analysis directly, then print the result.
        # For now, it's a placeholder as _analyze_health_with_agent handles the
        # interaction.
        print(
            f"  HealthCheckTask is analyzing results with {agent.__class__.__name__}")
        # In the future, this would call agent.generate with an analysis prompt
        # and process the response.
