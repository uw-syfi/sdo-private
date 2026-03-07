"""Shell execution tool for DSPy agents."""

import subprocess

DEFAULT_TIMEOUT = 120


def run_shell(command: str, cwd: str = ".", timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute a shell command and return its combined output.

    Args:
        command: The shell command to execute.
        cwd: Working directory for the command.
        timeout: Maximum seconds to wait before killing the process.

    Returns:
        A string containing the exit code, stdout, and stderr.
    """
    try:
        result = subprocess.run(  # noqa: S602 — shell=True is intentional
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        parts = [f"Exit code: {result.returncode}"]
        if result.stdout:
            parts.append(f"Stdout:\n{result.stdout}")
        if result.stderr:
            parts.append(f"Stderr:\n{result.stderr}")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout} seconds"
    except OSError as exc:
        return f"Error: {exc}"
