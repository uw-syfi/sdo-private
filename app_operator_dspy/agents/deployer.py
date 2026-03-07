"""DSPy-native deployment agent."""

import os
import stat

import dspy

from app_operator_dspy.signatures import (
    DiagnoseDeploymentFailure,
    GenerateDeployScript,
    GenerateHealthCheckScript,
)
from app_operator_dspy.tools.filesystem import write_file
from app_operator_dspy.tools.shell import run_shell

DEFAULT_MAX_ATTEMPTS = 5


class DeploymentAgent(dspy.Module):
    """Generates deployment scripts and self-heals on failure.

    Uses ChainOfThought for script generation and failure diagnosis.
    On each failure, the diagnosis is fed back as additional context
    for the next script generation attempt.
    """

    def __init__(self):
        super().__init__()
        self.gen_deploy = dspy.ChainOfThought(GenerateDeployScript)
        self.gen_health = dspy.ChainOfThought(GenerateHealthCheckScript)
        self.diagnose = dspy.ChainOfThought(DiagnoseDeploymentFailure)

    def forward(
        self,
        repo_path: str,
        code_analysis: str,
        deployment_issues: str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> dspy.Prediction:
        sds_dir = os.path.join(repo_path, ".sds")
        deploy_path = os.path.join(sds_dir, "deploy.sh")
        health_path = os.path.join(sds_dir, "health_check.sh")

        current_issues = deployment_issues

        for attempt in range(1, max_attempts + 1):
            # Generate and write scripts
            self._generate_scripts(repo_path, code_analysis, current_issues, deploy_path, health_path)

            # Run deploy
            deploy_output = run_shell(f"{deploy_path} start", cwd=repo_path)
            if not deploy_output.startswith("Exit code: 0"):
                if attempt == max_attempts:
                    return dspy.Prediction(success=False, attempts=attempt, error=deploy_output)
                current_issues = self._diagnose_and_update(
                    repo_path,
                    deploy_output,
                    attempt,
                    max_attempts,
                    deployment_issues,
                )
                continue

            # Run health check
            health_output = run_shell(health_path, cwd=repo_path)
            if health_output.startswith("Exit code: 0"):
                return dspy.Prediction(success=True, attempts=attempt)

            if attempt == max_attempts:
                return dspy.Prediction(success=False, attempts=attempt, error=health_output)

            error_context = f"Deploy output:\n{deploy_output}\n\nHealth check output:\n{health_output}"
            current_issues = self._diagnose_and_update(
                repo_path,
                error_context,
                attempt,
                max_attempts,
                deployment_issues,
            )

        return dspy.Prediction(success=False, attempts=max_attempts, error="max attempts reached")

    def _generate_scripts(
        self,
        repo_path: str,
        code_analysis: str,
        issues: str,
        deploy_path: str,
        health_path: str,
    ) -> None:
        deploy_result = self.gen_deploy(
            repo_path=repo_path,
            code_analysis=code_analysis,
            deployment_issues=issues,
        )
        health_result = self.gen_health(
            repo_path=repo_path,
            code_analysis=code_analysis,
            deployment_issues=issues,
        )
        write_file(deploy_path, deploy_result.deploy_script)
        write_file(health_path, health_result.health_check_script)
        os.chmod(deploy_path, os.stat(deploy_path).st_mode | stat.S_IEXEC)
        os.chmod(health_path, os.stat(health_path).st_mode | stat.S_IEXEC)

    def _diagnose_and_update(
        self,
        repo_path: str,
        error_output: str,
        attempt: int,
        max_attempts: int,
        base_issues: str,
    ) -> str:
        diag = self.diagnose(
            repo_path=repo_path,
            error_output=error_output,
            attempt=str(attempt),
            max_attempts=str(max_attempts),
        )
        return (
            f"{base_issues}\n\n--- Attempt {attempt} failure ---\n"
            f"Diagnosis: {diag.diagnosis}\nFix plan: {diag.fix_plan}"
        )
