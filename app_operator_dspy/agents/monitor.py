"""DSPy-native monitor agent."""

import dspy

from app_operator_dspy.constants import HEALTH_CHECK_TIMEOUT
from app_operator_dspy.signatures import AnalyzeHealthCheck
from app_operator_dspy.tools.health_check import run_health_check


class MonitorAgent(dspy.Module):
    """Runs health checks and analyzes the results.

    Uses ChainOfThought reasoning to classify application health
    and suggest remediation when issues are detected.
    """

    def __init__(self):
        super().__init__()
        self.analyze = dspy.ChainOfThought(AnalyzeHealthCheck)

    def forward(
        self,
        repo_path: str,
        check_number: int = 1,
        health_check_timeout: int = HEALTH_CHECK_TIMEOUT,
    ) -> dspy.Prediction:
        health_output = run_health_check(repo_path, timeout=health_check_timeout)
        result = self.analyze(
            health_output=health_output,
            check_number=check_number,
        )
        normalized = result.status.lower().strip()
        if normalized not in {"healthy", "degraded", "unhealthy"}:
            raise ValueError(f"status must be one of healthy/degraded/unhealthy, got: {result.status!r}")
        return dspy.Prediction(status=normalized, summary=result.summary, remediation=result.remediation)
