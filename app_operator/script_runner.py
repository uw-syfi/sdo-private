"""Shared script execution utilities."""

import subprocess
import time
from pathlib import Path

from app_operator.exceptions import FileSystemError, ProcessError
from app_operator.filesystem import FileSystemInterface
from app_operator.trajectory import TrajectoryRecorderProtocol
from app_operator.types import CommandResult


def write_log_file(filesystem: FileSystemInterface, path: Path, content: str) -> None:
    try:
        filesystem.mkdir(path.parent, parents=True, exist_ok=True)
        filesystem.write_text(path, content)
    except OSError as e:
        raise FileSystemError(f"Failed to write log file {path}: {e}") from e


def run_script(
    repo_path: Path,
    filesystem: FileSystemInterface,
    command: str,
    log_file_path: Path | None = None,
    timeout: int = 900,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> CommandResult:
    start_time = time.time()
    _process_error: ProcessError | None = None
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

    except subprocess.TimeoutExpired as e:
        success = False
        stdout = ""
        stderr = f"Command timed out after {timeout} seconds"
        exit_code = -1
        _process_error = ProcessError(stderr, exit_code=exit_code, timeout=True)
        _process_error.__cause__ = e

    except (OSError, subprocess.SubprocessError) as e:
        success = False
        stdout = ""
        stderr = f"Error: {e!s}"
        exit_code = -1
        _process_error = ProcessError(stderr, exit_code=exit_code)
        _process_error.__cause__ = e

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

    if _process_error is not None:
        raise _process_error

    return {
        "success": success,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }
