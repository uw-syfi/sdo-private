import subprocess
from pathlib import Path
from typing import Dict, Any, Optional


def run_health_check(
    repo_path: Path,
    health_check_script: Path,
    timeout: int = 120,
    log_file_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the health check script.

    Args:
        repo_path: Path to the repository.
        health_check_script: Path to the health check script.
        timeout: Timeout in seconds.
        log_file_path: Optional path to write health check outputs to.

    Returns:
        dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
    """
    print(f"Running health check: {health_check_script}")

    log_file = None
    if log_file_path:
        try:
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_file_path, "w")
            print(f"  Logging health check output to: {log_file_path}")
        except Exception as e:
            print(f"Warning: Could not open health check log file {log_file_path}: {e}")

    try:
        result = subprocess.run(
            [str(health_check_script)],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        status = "PASSED" if result.returncode == 0 else "FAILED"
        print(f"Health check finished: {status} (Exit Code: {result.returncode})")

        # Write outputs to log file if provided
        if log_file:
            try:
                log_file.write("=== Health Check Output ===\n")
                log_file.write(f"Exit Code: {result.returncode}\n")
                log_file.write(f"Status: {status}\n\n")
                if result.stdout:
                    log_file.write("=== STDOUT ===\n")
                    log_file.write(result.stdout)
                    log_file.write("\n")
                if result.stderr:
                    log_file.write("=== STDERR ===\n")
                    log_file.write(result.stderr)
                    log_file.write("\n")
                log_file.flush()
            except Exception as e:
                print(f"Warning: Could not write to health check log file: {e}")

        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired as e:
        error_msg = f"Health check timed out after {timeout} seconds"

        # Capture partial output
        stdout_output = e.stdout if e.stdout else ""
        stderr_output = e.stderr if e.stderr else error_msg

        if log_file:
            try:
                log_file.write("=== Health Check Timeout ===\n")
                log_file.write(f"{error_msg}\n")
                if stdout_output:
                    log_file.write("=== STDOUT (Partial) ===\n")
                    log_file.write(stdout_output)
                    log_file.write("\n")
                if stderr_output != error_msg and stderr_output:
                    log_file.write("=== STDERR (Partial) ===\n")
                    log_file.write(stderr_output)
                    log_file.write("\n")
                log_file.flush()
            except Exception:
                pass
        return {
            "success": False,
            "exit_code": -1,
            "stdout": stdout_output,
            "stderr": stderr_output,
        }
    except Exception as e:
        error_msg = f"Failed to run health check: {e}"
        if log_file:
            try:
                log_file.write("=== Health Check Error ===\n")
                log_file.write(f"{error_msg}\n")
                log_file.flush()
            except Exception:
                pass
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": error_msg}
    finally:
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass
