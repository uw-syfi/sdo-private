"""DSPy-native application operator."""

import dspy

from app_operator_dspy.agents.code_analyzer import CodeAnalyzerAgent
from app_operator_dspy.agents.deployer import DeploymentAgent
from app_operator_dspy.agents.monitor import MonitorAgent

DEFAULT_MODEL = "gemini/gemini-2.5-pro"


def configure_lm(model: str = DEFAULT_MODEL, **kwargs) -> dspy.LM:
    """Configure the DSPy language model globally.

    Args:
        model: LiteLLM model identifier (default: Gemini 2.5 Pro).
        **kwargs: Additional arguments passed to ``dspy.LM``.

    Returns:
        The configured ``dspy.LM`` instance.
    """
    lm = dspy.LM(model, **kwargs)
    dspy.configure(lm=lm)
    return lm


class DSPyOperator(dspy.Module):
    """Top-level orchestrator that runs analysis, deployment, and monitoring.

    Composes CodeAnalyzerAgent, DeploymentAgent, and MonitorAgent into
    a single end-to-end pipeline.
    """

    def __init__(self):
        super().__init__()
        self.analyzer = CodeAnalyzerAgent()
        self.deployer = DeploymentAgent()
        self.monitor = MonitorAgent()

    def forward(
        self,
        repo_path: str,
        max_deploy_attempts: int = 5,
        monitor_checks: int = 5,
    ) -> dspy.Prediction:
        # Step 1: Analyze codebase
        analysis = self.analyzer(repo_path=repo_path)

        # Step 2: Deploy
        deploy_result = self.deployer(
            repo_path=repo_path,
            code_analysis=analysis.analysis,
            deployment_issues=analysis.issues,
            max_attempts=max_deploy_attempts,
        )

        if not deploy_result.success:
            return dspy.Prediction(
                success=False,
                phase="deployment",
                error=deploy_result.error,
            )

        # Step 3: Monitor
        statuses = []
        for i in range(1, monitor_checks + 1):
            check = self.monitor(repo_path=repo_path, check_number=i)
            statuses.append(check.status)
            if check.status == "unhealthy":
                break

        return dspy.Prediction(success=True, phase="monitoring", statuses=statuses)
