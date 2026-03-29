"""LocalShellBackend subclass with robust timeout via process-group killing.

The upstream ``LocalShellBackend.execute()`` uses ``subprocess.run(shell=True,
capture_output=True, timeout=…)``.  When the timeout fires, Python sends
SIGKILL to the *shell* process, but grandchild processes (e.g. ``vi`` spawned
by ``kubectl edit``, or ``kubectl exec -it``) inherit the pipe file descriptors
and keep them open.  ``subprocess.run`` then blocks forever inside
``communicate()`` trying to drain the pipes.

This subclass replaces ``execute()`` with ``Popen`` +
``start_new_session=True`` so the entire process group can be killed on
timeout, preventing hangs from interactive / TTY-dependent commands.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess

from deepagents.backends.local_shell import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

logger = logging.getLogger(__name__)


class TimeoutShellBackend(LocalShellBackend):
    """LocalShellBackend that kills the whole process group on timeout."""

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        if not command or not isinstance(command, str):
            return ExecuteResponse(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
                truncated=False,
            )

        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout <= 0:
            msg = f"timeout must be positive, got {effective_timeout}"
            raise ValueError(msg)

        process: subprocess.Popen | None = None
        try:
            process = subprocess.Popen(  # noqa: S602
                command,
                shell=True,
                cwd=str(self.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._env,
                start_new_session=True,
            )
            stdout, stderr = process.communicate(timeout=effective_timeout)

            # Combine stdout and stderr (same format as upstream)
            output_parts = []
            if stdout:
                output_parts.append(stdout)
            if stderr:
                stderr_lines = stderr.strip().split("\n")
                output_parts.extend(f"[stderr] {line}" for line in stderr_lines)

            output = "\n".join(output_parts) if output_parts else "<no output>"

            truncated = False
            if len(output) > self._max_output_bytes:
                output = output[: self._max_output_bytes]
                output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
                truncated = True

            if process.returncode != 0:
                output = f"{output.rstrip()}\n\nExit code: {process.returncode}"

            return ExecuteResponse(
                output=output,
                exit_code=process.returncode,
                truncated=truncated,
            )

        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            if timeout is not None:
                msg = (
                    f"Error: Command timed out after {effective_timeout} "
                    "seconds (custom timeout). The command may be stuck."
                )
            else:
                msg = (
                    f"Error: Command timed out after {effective_timeout} "
                    "seconds. Re-run with a longer timeout if needed."
                )
            return ExecuteResponse(
                output=msg,
                exit_code=124,
                truncated=False,
            )

        except Exception as e:
            _kill_process_group(process)
            return ExecuteResponse(
                output=f"Error executing command ({type(e).__name__}): {e}",
                exit_code=1,
                truncated=False,
            )


def _kill_process_group(process: subprocess.Popen | None) -> None:
    """Kill the entire process group, falling back to process.kill()."""
    if process is None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except Exception:
        pass
