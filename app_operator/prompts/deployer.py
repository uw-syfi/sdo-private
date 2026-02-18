from pathlib import Path
from typing import Dict, Any, Optional
from app_operator.prompts import get_loader


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
        context_parts.append(
            f"Full deployment logs available at: {log_file_path}")

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
) -> str:
    """Create a prompt for generating deployment scripts.

    Args:
        system_prompt: The system prompt for the agent.
        script_name: The name of the script to generate (e.g., 'deploy.sh').
        repo_context: Context string describing the repository.
        target_dir: The directory where scripts will be generated.
        platform: The deployment platform (e.g., 'docker', 'kubernetes').

    Returns:
        str: The rendered prompt.
    """
    return get_loader().render(
        "deployer/generate_script.jinja2",
        system_prompt=system_prompt,
        script_name=script_name,
        repo_context=repo_context,
        target_dir=target_dir,
        platform=platform,
    )


def create_fix_prompt(
    repo_path: Path,
    attempt: int,
    max_attempts: int,
    error_context: str,
    deploy_script_path: Path,
    health_check_script_path: Path,
) -> str:
    """Create a prompt for the coding agent to fix deployment errors.

    Args:
        repo_path: Path to the repository.
        attempt: Current attempt number.
        max_attempts: Maximum number of attempts.
        error_context: Formatted error context.
        deploy_script_path: Path to the deploy script.
        health_check_script_path: Path to the health check script.

    Returns:
        str: The rendered prompt.
    """
    previous_summary_note = ""
    if attempt > 1:
        consolidated_summary_path = repo_path / ".sds" / "fix_summary.md"
        # Since we can't check file existence here (no filesystem access), we provide both paths
        # The agent can check which one exists.

        prev_log_path = repo_path / ".sds" / \
            "logs" / f"fix_summary_{attempt - 1}.log"

        previous_summary_note = (
            f"\n\nNote: This is attempt #{attempt}. "
            f"You can review the history of previous fixes at: {consolidated_summary_path}\n"
            f"Or the specific summary of the last attempt at: {prev_log_path}\n"
            "You can dive into prior attempts for more detail; logs follow the pattern: fix_summary_{attempt}.log"
            "Please review the previous attempts to avoid repeating mistakes."
        )

    return get_loader().render(
        "deployer/fix_error.jinja2",
        repo_path=repo_path,
        attempt=attempt,
        max_attempts=max_attempts,
        error_context=error_context,
        previous_summary_note=previous_summary_note,
        deploy_script=deploy_script_path,
        health_check_script=health_check_script_path,
    )


def create_consolidation_prompt(
    existing_summary: str,
    new_attempts_text: str,
) -> str:
    """Create a prompt for consolidating fix summaries.

    Args:
        existing_summary: The content of the existing fix_summary.md.
        new_attempts_text: Text describing the new attempts to integrate.

    Returns:
        str: The rendered prompt.
    """
    return get_loader().render(
        "deployer/consolidate_summary.jinja2",
        existing_summary=existing_summary,
        new_attempts_text=new_attempts_text,
    )
