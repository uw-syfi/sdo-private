import subprocess
import time
from pathlib import Path
from typing import Optional

from app_operator.ui import OperatorUI
from app_operator.logger import logger
from app_operator.types import CommandResult


def _write_to_log(log_file, header: str, stdout: str = "",
                  stderr: str = "") -> None:
    """Write a structured entry to a health check log file.

    Args:
        log_file: Open file handle to write to.
        header: Header text for the log entry.
        stdout: Standard output to include (omitted if empty).
        stderr: Standard error to include (omitted if empty).
    """
    try:
        log_file.write(header)
        if stdout:
            log_file.write("=== STDOUT ===\n")
            log_file.write(stdout)
            log_file.write("\n")
        if stderr:
            log_file.write("=== STDERR ===\n")
            log_file.write(stderr)
            log_file.write("\n")
        log_file.flush()
    except Exception as e:
        # Best-effort logging: swallow write errors so they don't mask the
        # health check result that is about to be returned to the caller.
        logger.debug("Failed to write health check log entry: %s", e)


def run_health_check(
    repo_path: Path,
    health_check_script: Path,
    timeout: int = 120,
    log_file_path: Optional[Path] = None,
    ui: Optional[OperatorUI] = None,
) -> CommandResult:
    """Run the health check script.

    Args:
        repo_path: Path to the repository.
        health_check_script: Path to the health check script.
        timeout: Timeout in seconds.
        log_file_path: Optional path to write health check outputs to.
        ui: Optional UI for tool events.

    Returns:
        CommandResult with keys 'success', 'exit_code', 'stdout', 'stderr'.
    """
    logger.info(f"Running health check: {health_check_script}")

    if ui:
        ui.on_tool_call("health_check.sh", {})

    log_file = None
    if log_file_path:
        try:
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_file_path, "w")
            logger.info(f"  Logging health check output to: {log_file_path}")
        except Exception as e:
            logger.warning(
                f"Could not open health check log file {log_file_path}: {e}")

    start_time = time.time()
    try:
        result = subprocess.run(
            [str(health_check_script)],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start_time

        status = "PASSED" if result.returncode == 0 else "FAILED"
        logger.info(
            f"Health check finished: {status} (Exit Code: {
                result.returncode})")

        if ui:
            ui.on_tool_result(
                tool="health_check.sh",
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration=duration,
            )

        # Write outputs to log file if provided
        if log_file:
            header = (
                "=== Health Check Output ===\n"
                f"Exit Code: {result.returncode}\n"
                f"Status: {status}\n\n"
            )
            _write_to_log(log_file, header, result.stdout, result.stderr)

        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired as e:
        duration = time.time() - start_time
        error_msg = f"Health check timed out after {timeout} seconds"

        # Capture partial output; e.stdout/e.stderr are bytes even when
        # text=True was passed to subprocess.run, so decode if needed.
        stdout_output = (
            e.stdout.decode(
                "utf-8",
                errors="replace") if isinstance(
                e.stdout,
                bytes) else e.stdout) or ""
        stderr_output = (
            e.stderr.decode(
                "utf-8",
                errors="replace") if isinstance(
                e.stderr,
                bytes) else e.stderr) or error_msg

        # Ensure outputs are strings (TimeoutExpired can return bytes sometimes
        # even with text=True depending on buffering/decoding state at timeout)
        if isinstance(stdout_output, bytes):
            stdout_output = stdout_output.decode("utf-8", errors="replace")
        if isinstance(stderr_output, bytes):
            stderr_output = stderr_output.decode("utf-8", errors="replace")

        if ui:
            ui.on_tool_result(
                tool="health_check.sh",
                stdout=stdout_output,
                stderr=stderr_output,
                exit_code=-1,
                duration=duration,
            )

        if log_file:
            header = f"=== Health Check Timeout ===\n{error_msg}\n"
            # For timeout, only include stderr if it differs from the error
            # message
            timeout_stderr = stderr_output if stderr_output != error_msg else ""
            _write_to_log(
                log_file, header,
                stdout=stdout_output,
                stderr=timeout_stderr,
            )

        return {
            "success": False,
            "exit_code": -1,
            "stdout": stdout_output,
            "stderr": stderr_output,
        }
    except Exception as e:
        duration = time.time() - start_time
        error_msg = f"Failed to run health check: {e}"

        if ui:
            ui.on_tool_result(
                tool="health_check.sh",
                stdout="",
                stderr=error_msg,
                exit_code=-1,
                duration=duration,
            )

        if log_file:
            header = f"=== Health Check Error ===\n{error_msg}\n"
            _write_to_log(log_file, header)

        return {"success": False, "exit_code": -
                1, "stdout": "", "stderr": error_msg}
    finally:
        if log_file:
            try:
                log_file.close()
            except Exception as e:
                # Best-effort cleanup: swallow close errors so the caller
                # receives the health check result unaffected.
                logger.debug("Failed to close health check log file: %s", e)
