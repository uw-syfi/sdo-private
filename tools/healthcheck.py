import subprocess
import sys
from pathlib import Path
from typing import Dict, Any


def run_health_check(repo_path: Path, health_check_script: Path,
                     timeout: int = 120) -> Dict[str, Any]:
    """Run the health check script.

    Args:
        repo_path: Path to the repository.
        health_check_script: Path to the health check script.
        timeout: Timeout in seconds.

    Returns:
        dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
    """
    print(f"Running health check: {health_check_script}")

    try:
        result = subprocess.run(
            [str(health_check_script)],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # Print output
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Health check timed out after {timeout} seconds"
        }
    except Exception as e:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Failed to run health check: {e}"
        }
