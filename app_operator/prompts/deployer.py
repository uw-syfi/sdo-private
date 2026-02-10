from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING

from app_operator.prompts import get_loader

if TYPE_CHECKING:
    from app_operator.dspy_integration.config import DSPyConfig


def prepare_error_context(
    deploy_result: Dict[str, Any],
    health_result: Optional[Dict[str, Any]],
    log_file_path: Optional[Path] = None,
    health_check_log_path: Optional[Path] = None,
) -> str:
    """Prepare error context for the coding agent.

    Args:
        deploy_result: Deployment script result.
        health_result: Health check result (None if deployment failed).
        log_file_path: Path to the deployment log file.
        health_check_log_path: Path to the health check log file.

    Returns:
        str: Formatted error context.
    """
    context_parts = []

    if log_file_path:
        context_parts.append(f"Full deployment logs available at: {log_file_path}")

    if health_check_log_path:
        context_parts.append(
            f"Health check outputs available at: {health_check_log_path}"
        )

    # Deployment result
    context_parts.append("## Deployment Script Result")
    context_parts.append(f"Exit Code: {deploy_result['exit_code']}")
    context_parts.append(
        f"Status: {'SUCCESS' if deploy_result['success'] else 'FAILED'}"
    )

    if health_result is not None:
        context_parts.append("\n## Health Check Result")
        context_parts.append(f"Exit Code: {health_result['exit_code']}")
        context_parts.append(
            f"Status: {'SUCCESS' if health_result['success'] else 'FAILED'}"
        )

    return "\n".join(context_parts)


def create_generate_script_prompt(
    system_prompt: str,
    script_name: str,
    repo_context: str,
    target_dir: str,
    platform: str,
    dspy_config: Optional["DSPyConfig"] = None,
    recorder=None,
) -> str:
    """Create a prompt for generating deployment scripts.

    Args:
        system_prompt: The system prompt for the agent.
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
        template_name = "deployer/generate_deploy_script.jinja2"
    elif script_name == "health_check.sh":
        template_name = "deployer/generate_health_check.jinja2"
    else:
        # Fallback for other scripts or backward compatibility
        template_name = "deployer/generate_script.jinja2"

    # Read code analysis and deployment issues if available
    # This ensures kwargs match the DSPy signatures for optimization
    code_analysis = ""
    deployment_issues = ""
    try:
        sds_dir = Path(target_dir) / ".sds"
        ca_path = sds_dir / "code_analysis.md"
        di_path = sds_dir / "deployment_issues.md"

        if ca_path.exists():
            code_analysis = ca_path.read_text()
        if di_path.exists():
            deployment_issues = di_path.read_text()
    except Exception:
        # Ignore filesystem errors during prompt generation
        pass

    return get_loader(dspy_config).render(
        template_name,
        system_prompt=system_prompt,
        script_name=script_name,
        repo_context=repo_context,
        target_dir=target_dir,
        repo_path=target_dir,  # Map target_dir to repo_path for signature
        code_analysis=code_analysis,
        deployment_issues=deployment_issues,
        platform=platform,
        _trajectory_recorder=recorder,
    )


def create_fix_prompt(
    repo_path: Path,
    attempt: int,
    max_attempts: int,
    error_context: str,
    deploy_script_path: Path,
    health_check_script_path: Path,
    dspy_config: Optional["DSPyConfig"] = None,
    recorder=None,
) -> str:
    """Create a prompt for the coding agent to fix deployment errors.

    Args:
        repo_path: Path to the repository.
        attempt: Current attempt number.
        max_attempts: Maximum number of attempts.
        error_context: Formatted error context.
        deploy_script_path: Path to the deploy script.
        health_check_script_path: Path to the health check script.
        dspy_config: Optional DSPy configuration for optimized prompts.
        recorder: Optional trajectory recorder for kwargs capture.

    Returns:
        str: The rendered prompt.
    """
    previous_summary_note = ""
    if attempt > 1:
        prev_log_path = repo_path / ".sds" / "logs" / f"fix_summary_{attempt - 1}.log"
        previous_summary_note = (
            f"\n\nNote: This is attempt #{attempt}. "
            f"You can read the summary of the previous fix attempt at:\n{prev_log_path}\n"
            "The log files follow the pattern .sds/logs/fix_summary_{attempt}.log. "
            "Please review the previous attempt to avoid repeating mistakes, and "
            "to check if the previous fix was successful."
            "Note that the application may still be failing, but the it's now "
            "failing for a different reason."
        )

    return get_loader(dspy_config).render(
        "deployer/fix_error.jinja2",
        repo_path=repo_path,
        attempt=attempt,
        max_attempts=max_attempts,
        error_context=error_context,
        previous_summary_note=previous_summary_note,
        deploy_script=deploy_script_path,
        health_check_script=health_check_script_path,
        _trajectory_recorder=recorder,
    )
