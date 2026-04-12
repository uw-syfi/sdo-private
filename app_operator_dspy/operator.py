"""DSPy-native application operator."""

import dspy

from app_operator_dspy.agents.code_analyzer import CodeAnalyzerAgent
from app_operator_dspy.agents.deployer import DeploymentAgent
from app_operator_dspy.agents.monitor import MonitorAgent
from app_operator_dspy.constants import DEPLOY_TIMEOUT, HEALTH_CHECK_TIMEOUT
from app_operator_dspy.logger import get_logger

log = get_logger("operator")


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
        deploy_timeout: int = DEPLOY_TIMEOUT,
        health_check_timeout: int = HEALTH_CHECK_TIMEOUT,
        platform: str = "docker",
    ) -> dspy.Prediction:
        # Step 1: Analyze codebase
        log.info("code_analysis — analyzing repository...")
        analysis = self.analyzer(repo_path=repo_path)
        log.info("code_analysis — done")

        # Step 2: Deploy
        log.info("deployment — up to {} attempts", max_deploy_attempts)
        deploy_result = self.deployer(
            repo_path=repo_path,
            code_analysis=analysis.analysis,
            deployment_issues=analysis.issues,
            max_attempts=max_deploy_attempts,
            deploy_timeout=deploy_timeout,
            platform=platform,
        )

        if not deploy_result.success:
            log.info("deployment — failed after {} attempts", deploy_result.attempts)
            return dspy.Prediction(
                success=False,
                phase="deployment",
                attempts=deploy_result.attempts,
                error=deploy_result.error,
            )

        log.info("deployment — succeeded on attempt {}", deploy_result.attempts)

        # Step 3: Monitor
        log.info("monitoring — {} checks", monitor_checks)
        statuses = []
        for i in range(1, monitor_checks + 1):
            check = self.monitor(
                repo_path=repo_path,
                check_number=i,
                health_check_timeout=health_check_timeout,
            )
            statuses.append(check.status)
            log.info("monitor check {}/{}: {}", i, monitor_checks, check.status)
            if check.status == "unhealthy":
                break

        return dspy.Prediction(
            success=True,
            phase="monitoring",
            attempts=deploy_result.attempts,
            statuses=statuses,
        )
