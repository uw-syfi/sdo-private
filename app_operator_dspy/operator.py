"""DSPy-native application operator."""

import dspy

from app_operator_dspy.agents.code_analyzer import CodeAnalyzerAgent
from app_operator_dspy.agents.deployer import DeploymentAgent
from app_operator_dspy.agents.monitor import MonitorAgent

def configure_lm(model: str, **kwargs) -> dspy.LM:
    """Configure the DSPy language model globally.

    Args:
        model: LiteLLM model identifier.
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

    def __init__(
        self,
        max_file_size: int = 100_000,
        max_context_chars: int = 500_000,
    ):
        super().__init__()
        self.analyzer = CodeAnalyzerAgent(
            max_file_size=max_file_size,
            max_context_chars=max_context_chars,
        )
        self.deployer = DeploymentAgent()
        self.monitor = MonitorAgent()

    def forward(
        self,
        repo_path: str,
        max_deploy_attempts: int = 5,
        monitor_checks: int = 5,
        deploy_timeout: int = 300,
        health_check_timeout: int = 300,
    ) -> dspy.Prediction:
        # Step 1: Analyze codebase
        print("[phase] code_analysis — analyzing repository...")
        analysis = self.analyzer(repo_path=repo_path)
        print("[phase] code_analysis — done")

        # Step 2: Deploy
        print(f"[phase] deployment — up to {max_deploy_attempts} attempts")
        deploy_result = self.deployer(
            repo_path=repo_path,
            code_analysis=analysis.analysis,
            deployment_issues=analysis.issues,
            max_attempts=max_deploy_attempts,
            deploy_timeout=deploy_timeout,
            health_check_timeout=health_check_timeout,
        )

        if not deploy_result.success:
            print(f"[phase] deployment — failed after {deploy_result.attempts} attempts")
            return dspy.Prediction(
                success=False,
                phase="deployment",
                error=deploy_result.error,
            )

        print(f"[phase] deployment — succeeded on attempt {deploy_result.attempts}")

        # Step 3: Monitor
        print(f"[phase] monitoring — {monitor_checks} checks")
        statuses = []
        for i in range(1, monitor_checks + 1):
            check = self.monitor(
                repo_path=repo_path,
                check_number=i,
                health_check_timeout=health_check_timeout,
            )
            statuses.append(check.status)
            print(f"[phase] monitor check {i}/{monitor_checks}: {check.status}")
            if check.status == "unhealthy":
                break

        return dspy.Prediction(success=True, phase="monitoring", statuses=statuses)
