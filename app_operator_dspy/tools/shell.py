"""Shell execution tool for DSPy agents."""

import subprocess

from app_operator_dspy.constants import SHELL_DEFAULT_TIMEOUT

DEFAULT_TIMEOUT = SHELL_DEFAULT_TIMEOUT


def _format_output(return_code: int, stdout: str = "", stderr: str = "") -> str:
    """Build formatted output string for ShellResult."""
    parts = [f"Exit code: {return_code}"]
    if stdout:
        parts.append(f"Stdout:\n{stdout}")
    if stderr:
        parts.append(f"Stderr:\n{stderr}")
    return "\n".join(parts)


class ShellResult:
    """Structured result from a shell command."""

    def __init__(self, return_code: int, output: str) -> None:
        self.return_code = return_code
        self.output = output

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0

    def __str__(self) -> str:
        return self.output


def run_shell(command: str, cwd: str = ".", timeout: int = DEFAULT_TIMEOUT) -> ShellResult:
    """Execute a shell command and return structured result.

    Args:
        command: The shell command to execute.
        cwd: Working directory for the command.
        timeout: Maximum seconds to wait before killing the process.

    Returns:
        ShellResult with return_code and formatted output.
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
        output = _format_output(
            result.returncode,
            result.stdout or "",
            result.stderr or "",
        )
        return ShellResult(result.returncode, output)
    except subprocess.TimeoutExpired:
        output = f"Exit code: 124\nStderr:\nCommand timed out after {timeout} seconds"
        return ShellResult(124, output)
    except OSError as exc:
        output = f"Exit code: 1\nStderr:\n{exc}"
        return ShellResult(1, output)
