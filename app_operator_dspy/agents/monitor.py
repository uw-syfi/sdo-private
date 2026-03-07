"""DSPy-native monitor agent."""

import dspy

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

    def forward(self, repo_path: str, check_number: int = 1) -> dspy.Prediction:
        health_output = run_health_check(repo_path)
        return self.analyze(
            health_output=health_output,
            check_number=str(check_number),
        )
