from __future__ import annotations

from pathlib import Path

from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.prompts import DSPyConfigProtocol, get_loader
from app_operator.types import CommandResult

FIX_SUMMARY_FILENAME = "fix_summary.md"


def prepare_error_context(
    deploy_result: CommandResult,
    health_result: CommandResult | None,
    log_file_path: Path | None = None,
    health_check_log_path: Path | None = None,
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
        context_parts.append(f"Health check outputs available at: {health_check_log_path}")

    # Deployment result
    context_parts.append("## Deployment Script Result")
    context_parts.append(f"Exit Code: {deploy_result['exit_code']}")
    context_parts.append(f"Status: {'SUCCESS' if deploy_result['success'] else 'FAILED'}")

    if health_result is not None:
        context_parts.append("\n## Health Check Result")
        context_parts.append(f"Exit Code: {health_result['exit_code']}")
        context_parts.append(f"Status: {'SUCCESS' if health_result['success'] else 'FAILED'}")

    return "\n".join(context_parts)


def create_generate_script_prompt(
    system_prompt: str,
    script_name: str,
    repo_context: str,
    target_dir: str,
    platform: str,
    dspy_config: DSPyConfigProtocol | None = None,
    recorder=None,
    filesystem: FileSystemInterface | None = None,
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
        filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.

    Returns:
        str: The rendered prompt.
    """
    if filesystem is None:
        filesystem = RealFilesystem()

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

        if filesystem.exists(ca_path):
            code_analysis = filesystem.read_text(ca_path)
        if filesystem.exists(di_path):
            deployment_issues = filesystem.read_text(di_path)
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
    fix_summary_consolidation: bool = True,
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
        fix_summary_consolidation: Whether consolidated fix summary is enabled.

    Returns:
        str: The rendered prompt.
    """
    previous_summary_note = ""
    if attempt > 1:
        consolidated_summary_path = repo_path / ".sds" / FIX_SUMMARY_FILENAME
        prev_log_path = repo_path / ".sds" / "logs" / f"fix_summary_{attempt - 1}.log"

        if fix_summary_consolidation:
            previous_summary_note = (
                f"\n\nNote: This is attempt #{attempt}. "
                f"You can review the history of previous fixes at: {consolidated_summary_path}\n"
                f"Or the specific summary of the last attempt at: {prev_log_path}\n"
                "You can dive into prior attempts for more detail; logs follow the pattern: fix_summary_{attempt}.log"
                "Please review the previous attempts to avoid repeating mistakes."
            )
        else:
            previous_summary_note = (
                f"\n\nNote: This is attempt #{attempt}. "
                f"You can review the summary of the last attempt at: {prev_log_path}\n"
                "You can dive into prior attempts for more detail; logs follow the pattern: fix_summary_{attempt}.log"
                "Please review the previous attempts to avoid repeating mistakes."
            )

    has_deployment_issues = (repo_path / ".sds" / "deployment_issues.md").exists()
    has_code_analysis = (repo_path / ".sds" / "code_analysis.md").exists()

    return get_loader(dspy_config).render(
        "deployer/fix_error.jinja2",
        repo_path=repo_path,
        attempt=attempt,
        max_attempts=max_attempts,
        error_context=error_context,
        previous_summary_note=previous_summary_note,
        deploy_script=deploy_script_path,
        health_check_script=health_check_script_path,
        platform=platform,
        recorder=recorder,
        has_deployment_issues=has_deployment_issues,
        has_code_analysis=has_code_analysis,
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
