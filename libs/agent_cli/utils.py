import os
import subprocess
from typing import Any


def get_interactive_env() -> dict[str, str]:
    """Capture environment variables from an interactive shell."""
    try:
        # Run env in an interactive shell to get the full user environment
        # Use start_new_session=True (setsid) to detach from TTY and avoid
        # SIGTTOU/SIGTTIN signals when bash -i tries to set process group
        result = subprocess.run(
            ["/bin/bash", "-i", "-c", "env"], capture_output=True, text=True, check=False, start_new_session=True
        )
        if result.returncode != 0:
            return os.environ.copy()

        env: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                env[key] = value
        return env
    except Exception:
        return os.environ.copy()


def truncate_params(params: Any, max_len: int = 200) -> str:
    """Truncate a parameter representation to *max_len* characters."""
    s = str(params)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def truncate_content(content: str, max_lines: int = 10) -> str:
    """Truncate *content* keeping the first and last *max_lines* lines."""
    lines = content.splitlines()
    if len(lines) > max_lines * 2:
        return "\n".join(lines[:max_lines] + ["... (truncated) ..."] + lines[-max_lines:])
    return content
