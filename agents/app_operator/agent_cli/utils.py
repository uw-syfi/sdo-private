
import os
import subprocess
from typing import Dict


def _get_interactive_env() -> Dict[str, str]:
    """Capture environment variables from an interactive shell."""
    try:
        # Run env in an interactive shell to get the full user environment
        # Use start_new_session=True (setsid) to detach from TTY and avoid
        # SIGTTOU/SIGTTIN signals when bash -i tries to set process group
        result = subprocess.run(
            ["/bin/bash", "-i", "-c", "env"],
            capture_output=True,
            text=True,
            check=False,
            start_new_session=True
        )
        if result.returncode != 0:
            return os.environ.copy()

        env = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                env[key] = value
        return env
    except Exception:
        return os.environ.copy()
