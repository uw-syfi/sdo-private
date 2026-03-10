from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app_operator.prompts._core import DSPyConfigProtocol, get_loader

if TYPE_CHECKING:
    from app_operator.types import CommandResult, HealthVerdict


def prepare_error_context(
    deploy_result: CommandResult,
    health_verdict: HealthVerdict | None,
    log_file_path: Path | None = None,
    health_check_log_path: Path | None = None,
) -> str:
    """Prepare error context for the coding agent.

    Args:
        deploy_result: Deployment script result.
        health_verdict: Health verdict (None if deployment failed before health check).
        log_file_path: Path to the deployment log file.
        health_check_log_path: Path to the health check log file.

    Returns:
        str: Formatted error context.
    """
    context_parts = []

    if log_file_path:
        context_parts.append(f"Full deployment logs available at: {log_file_path}")

    if health_check_log_path:
        context_parts.append(f"Health check outputs available at: {health_check_log_path}")

    # Deployment result
    context_parts.append("## Deployment Script Result")
    context_parts.append(f"Exit Code: {deploy_result['exit_code']}")
    context_parts.append(f"Status: {'SUCCESS' if deploy_result['success'] else 'FAILED'}")

    if health_verdict is not None:
        context_parts.append("\n## Health Assessment")
        context_parts.append(f"Status: {'HEALTHY' if health_verdict.healthy else 'UNHEALTHY'}")
        if health_verdict.diagnosis:
            context_parts.append(f"Diagnosis: {health_verdict.diagnosis}")
        if health_verdict.assessment:
            context_parts.append(f"Assessment: {health_verdict.assessment}")

    return "\n".join(context_parts)


def create_generate_script_prompt(
    script_name: str,
    repo_context: str,
    target_dir: str,
    platform: str,
    dspy_config: DSPyConfigProtocol | None = None,
    recorder=None,
) -> str:
    """Create a prompt for generating deployment scripts.

    Args:
        script_name: The name of the script to generate (e.g., 'deploy.sh').
        repo_context: Context string describing the repository.
        target_dir: The directory where scripts will be generated.
        platform: The deployment platform (e.g., 'docker', 'kubernetes').
        dspy_config: Optional DSPy configuration for optimized prompts.
        recorder: Optional trajectory recorder for kwargs capture.

    Returns:
        str: The rendered prompt.
    """
    if script_name == "deploy.sh":
        template_name = "script_generator/deploy_user.jinja2"
    elif script_name == "health_check.sh":
        template_name = "script_generator/health_check_user.jinja2"
    else:
        # Fallback for other scripts or backward compatibility
        template_name = "script_generator/user.jinja2"

    sds_dir = Path(target_dir) / ".sds"
    has_code_analysis = (sds_dir / "code_analysis.md").exists()
    has_deployment_issues = (sds_dir / "deployment_issues.md").exists()

    return get_loader(dspy_config).render(
        template_name,
        script_name=script_name,
        repo_context=repo_context,
        target_dir=target_dir,
        repo_path=target_dir,  # Map target_dir to repo_path for signature
        has_code_analysis=has_code_analysis,
        has_deployment_issues=has_deployment_issues,
        platform=platform,
        recorder=recorder,
    )


def create_fix_prompt(
    repo_path: Path,
    attempt: int,
    max_attempts: int,
    error_context: str,
    deploy_script_path: Path,
    health_check_script_path: Path,
    platform: str = "auto",
    dspy_config: DSPyConfigProtocol | None = None,
    recorder=None,
    deployment_progress_path: Path | None = None,
    structured_output: bool = False,
) -> str:
    """Create a prompt for the coding agent to fix deployment errors.

    Args:
        repo_path: Path to the repository.
        attempt: Current attempt number.
        max_attempts: Maximum number of attempts.
        error_context: Formatted error context.
        deploy_script_path: Path to the deploy script.
        health_check_script_path: Path to the health check script.
        platform: Deployment platform (e.g., 'docker', 'k8s').
        dspy_config: Optional DSPy configuration for optimized prompts.
        recorder: Optional trajectory recorder for kwargs capture.
        deployment_progress_path: Path to deployment_progress.md (None if feature disabled).
        structured_output: If True, instruct the agent to use structured output instead of XML tags.

    Returns:
        str: The rendered prompt.
    """
    has_deployment_issues = (repo_path / ".sds" / "deployment_issues.md").exists()
    has_code_analysis = (repo_path / ".sds" / "code_analysis.md").exists()
    has_deployment_progress = deployment_progress_path is not None and deployment_progress_path.exists()

    return get_loader(dspy_config).render(
        "repair_agent/user.jinja2",
        repo_path=repo_path,
        attempt=attempt,
        max_attempts=max_attempts,
        error_context=error_context,
        deploy_script=deploy_script_path,
        health_check_script=health_check_script_path,
        platform=platform,
        recorder=recorder,
        has_deployment_issues=has_deployment_issues,
        has_code_analysis=has_code_analysis,
        has_deployment_progress=has_deployment_progress,
        deployment_progress_path=deployment_progress_path,
        structured_output=structured_output,
    )


def create_fix_system_prompt() -> str:
    """Create the system prompt for the repair agent."""
    return get_loader().render("repair_agent/system.jinja2")


def create_health_system_prompt() -> str:
    """Create the system prompt for the health judge agent."""
    return get_loader().render("health_judge_agent/system.jinja2")
