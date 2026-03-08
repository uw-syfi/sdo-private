"""Health check tool for DSPy agents."""

from app_operator_dspy.tools.shell import DEFAULT_TIMEOUT, run_shell


def run_health_check(repo_path: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run the health check script for a deployed application.

    Executes ``<repo_path>/.sds/health_check.sh`` and returns the result.

    Args:
        repo_path: Path to the repository whose health check to run.
        timeout: Maximum seconds to wait.

    Returns:
        Combined output including exit code, stdout, and stderr.
    """
    script = f"{repo_path}/.sds/health_check.sh"
    return run_shell(script, cwd=repo_path, timeout=timeout)
