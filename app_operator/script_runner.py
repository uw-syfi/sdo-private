"""Shared script execution utilities.

Promoted from ``app_operator.langgraph.utils`` so that multiple runtimes
(langgraph, pydantic_ai, …) can reuse them without pulling in LangChain.
"""

import subprocess
import time
from pathlib import Path
from typing import Any

from app_operator.filesystem import FileSystemInterface
from app_operator.trajectory import TrajectoryRecorderProtocol


def write_log_file(filesystem: FileSystemInterface, path: Path, content: str) -> None:
    filesystem.mkdir(path.parent, parents=True, exist_ok=True)
    filesystem.write_text(path, content)


def run_script(
    repo_path: Path,
    filesystem: FileSystemInterface,
    command: str,
    log_file_path: Path | None = None,
    timeout: int = 900,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> dict[str, Any]:
    start_time = time.time()
    try:
        result = subprocess.run(  # noqa: S602 — shell=True required for agent commands
            command,
            cwd=str(repo_path),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        success = result.returncode == 0
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode

    except subprocess.TimeoutExpired:
        success = False
        stdout = ""
        stderr = f"Command timed out after {timeout} seconds"
        exit_code = -1

    except (OSError, subprocess.SubprocessError) as e:
        success = False
        stdout = ""
        stderr = f"Error: {e!s}"
        exit_code = -1

    duration = time.time() - start_time

    if log_file_path:
        log_content = (
            f"=== Command ===\n{command}\n\n"
            f"=== Exit Code ===\n{exit_code}\n\n"
            f"=== STDOUT ===\n{stdout}\n\n"
            f"=== STDERR ===\n{stderr}\n"
        )
        write_log_file(filesystem, log_file_path, log_content)

    if recorder:
        recorder.add_tool_call(
            tool="bash",
            args={"command": command},
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration=duration,
        )

    return {
        "success": success,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }
