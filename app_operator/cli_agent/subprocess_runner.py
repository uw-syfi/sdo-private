"""Subprocess runner with threading, timeout handling, and progress monitoring."""

import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable


class SubprocessRunner:
    """Manages subprocess lifecycle with threading for output capture.

    This class handles:
    - Running a subprocess with real-time output capture
    - Timeout management
    - Shutdown signal handling
    - Thread-safe log file writing
    """

    def __init__(
        self,
        command: List[str],
        cwd: str,
        timeout: int,
        log_file_path: Optional[Path] = None,
        check_shutdown: Optional[Callable[[], bool]] = None,
        time_func: Optional[Callable[[], float]] = None,
        sleep_func: Optional[Callable[[float], None]] = None,
        popen_func: Optional[Callable] = None,
    ):
        """Initialize the subprocess runner.

        Args:
            command: Command and arguments to execute.
            cwd: Working directory for the subprocess.
            timeout: Timeout in seconds.
            log_file_path: Optional path to write output logs to.
            check_shutdown: Optional callable returning True if shutdown requested.
            time_func: Optional function to get current time (default: time.time).
            sleep_func: Optional function to sleep (default: time.sleep).
            popen_func: Optional function to create subprocess (default: self.popen_func).
        """
        self.command = command
        self.cwd = cwd
        self.timeout = timeout
        self.log_file_path = log_file_path
        self.check_shutdown = check_shutdown
        self.time_func = time_func if time_func is not None else time.time
        self.sleep_func = sleep_func if sleep_func is not None else time.sleep
        self.popen_func = popen_func if popen_func is not None else subprocess.Popen

        self.process: Optional[self.popen_func] = None
        self.stdout_lines: List[str] = []
        self.stderr_lines: List[str] = []
        self._log_file = None
        self._log_lock = threading.Lock()

    def run(self) -> Dict[str, Any]:
        """Run the subprocess and return results.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        # Open log file if provided
        if self.log_file_path:
            try:
                self._log_file = open(self.log_file_path, "w")
            except (OSError, IOError):
                # Log file open failed - continue without logging
                pass

        try:
            # Start subprocess
            self.process = self.popen_func(
                self.command,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )

            # Start threads to read stdout and stderr
            stdout_thread = threading.Thread(
                target=self._read_pipe, args=(self.process.stdout, self.stdout_lines)
            )
            stderr_thread = threading.Thread(
                target=self._read_pipe, args=(self.process.stderr, self.stderr_lines)
            )

            stdout_thread.daemon = True
            stderr_thread.daemon = True

            stdout_thread.start()
            stderr_thread.start()

            # Wait for process to complete or timeout/shutdown
            result = self._wait_for_completion()

            # Wait for threads to finish reading
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

            return result

        except (OSError, subprocess.SubprocessError) as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Failed to run deployment script: {e}",
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Unexpected error running deployment script: {e}",
            }
        finally:
            if self._log_file:
                self._log_file.close()
            self._ensure_process_terminated()

    def _read_pipe(self, pipe, buffer: List[str]):
        """Read pipe line by line and capture to buffer.

        Args:
            pipe: The pipe to read from (stdout or stderr).
            buffer: List to append lines to.
        """
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                buffer.append(line)

                if self._log_file:
                    with self._log_lock:
                        self._log_file.write(line)
                        self._log_file.flush()
        except ValueError:
            pass  # Handle closed file
        finally:
            pipe.close()

    def _wait_for_completion(self) -> Dict[str, Any]:
        """Wait for process completion with timeout and shutdown checks.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        start_time = self.time_func()

        # Handle timeout=0 as a special case that fails immediately
        if self.timeout == 0:
            self._ensure_process_terminated()
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "".join(self.stdout_lines),
                "stderr": "Deployment script timed out after 0 seconds\n"
                + "".join(self.stderr_lines),
            }

        while True:
            current_time = self.time_func()
            elapsed = current_time - start_time

            # Check timeout
            if elapsed >= self.timeout:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                    return {
                        "success": False,
                        "exit_code": -1,
                        "stdout": "".join(self.stdout_lines),
                        "stderr": f"Deployment script timed out after {self.timeout} seconds\n"
                        + "".join(self.stderr_lines),
                    }

            # Check if process completed
            if self.process.poll() is not None:
                break

            # Check shutdown
            if self.check_shutdown and self.check_shutdown():
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                return {
                    "success": False,
                    "exit_code": -1,
                    "stdout": "".join(self.stdout_lines),
                    "stderr": "Deployment interrupted by shutdown request\n"
                    + "".join(self.stderr_lines),
                }

            # Yield to allow other processing
            self.sleep_func(0.1)

        # Process completed
        stdout_data = "".join(self.stdout_lines)
        stderr_data = "".join(self.stderr_lines)

        return {
            "success": self.process.returncode == 0,
            "exit_code": self.process.returncode,
            "stdout": stdout_data,
            "stderr": stderr_data,
        }

    def _ensure_process_terminated(self):
        """Ensure process is terminated (cleanup)."""
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except (subprocess.TimeoutExpired, Exception):
                try:
                    self.process.kill()
                except Exception:
                    pass

    def get_recent_output(self, num_lines: int = 20) -> str:
        """Get recent output from stdout and stderr.

        Args:
            num_lines: Number of recent lines to retrieve from each stream.

        Returns:
            str: Combined recent output.
        """
        recent_stdout = "".join(self.stdout_lines[-num_lines:])
        recent_stderr = "".join(self.stderr_lines[-num_lines:])
        return f"{recent_stdout}\n{recent_stderr}"

    def is_running(self) -> bool:
        """Check if the process is still running.

        Returns:
            bool: True if process is running, False otherwise.
        """
        return self.process is not None and self.process.poll() is None

    def run_with_progress_monitoring(self, summarizer) -> Dict[str, Any]:
        """Run subprocess with integrated progress monitoring.

        This method starts the subprocess and monitors it, providing periodic
        progress summaries via the given summarizer.

        Args:
            summarizer: ProgressSummarizer instance for generating summaries.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        # Open log file if provided
        if self.log_file_path:
            try:
                self._log_file = open(self.log_file_path, "w")
            except (OSError, IOError):
                # Log file open failed - continue without logging
                pass

        try:
            # Start subprocess
            self.process = self.popen_func(
                self.command,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )

            # Start threads to read stdout and stderr
            stdout_thread = threading.Thread(
                target=self._read_pipe, args=(self.process.stdout, self.stdout_lines)
            )
            stderr_thread = threading.Thread(
                target=self._read_pipe, args=(self.process.stderr, self.stderr_lines)
            )

            stdout_thread.daemon = True
            stderr_thread.daemon = True

            stdout_thread.start()
            stderr_thread.start()

            # Wait for process with progress monitoring (starts summarizer timer internally)
            result = self._wait_with_progress(summarizer)

            # Wait for threads to finish reading
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

            return result

        except (OSError, subprocess.SubprocessError) as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Failed to run deployment script: {e}",
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Unexpected error running deployment script: {e}",
            }
        finally:
            if self._log_file:
                self._log_file.close()
            self._ensure_process_terminated()

    def _wait_with_progress(self, summarizer) -> Dict[str, Any]:
        """Wait for process completion with progress monitoring.

        Args:
            summarizer: ProgressSummarizer instance for generating summaries.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        start_time = self.time_func()
        # Initialize summarizer with the same start time to avoid extra time_func call
        summarizer.start(start_time=start_time)

        # Handle timeout=0 as a special case that fails immediately
        if self.timeout == 0:
            self._ensure_process_terminated()
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "".join(self.stdout_lines),
                "stderr": "Deployment script timed out after 0 seconds\n"
                + "".join(self.stderr_lines),
            }

        while True:
            current_time = self.time_func()
            elapsed = current_time - start_time

            # Check timeout
            if elapsed >= self.timeout:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                    return {
                        "success": False,
                        "exit_code": -1,
                        "stdout": "".join(self.stdout_lines),
                        "stderr": f"Deployment script timed out after {self.timeout} seconds\n"
                        + "".join(self.stderr_lines),
                    }

            # Check if process completed
            if self.process.poll() is not None:
                break

            # Check shutdown
            if self.check_shutdown and self.check_shutdown():
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                return {
                    "success": False,
                    "exit_code": -1,
                    "stdout": "".join(self.stdout_lines),
                    "stderr": "Deployment interrupted by shutdown request\n"
                    + "".join(self.stderr_lines),
                }

            # Check for progress summary
            if summarizer.should_summarize():
                recent_output = self.get_recent_output(num_lines=20)
                summarizer.summarize(recent_output)

            # Yield to allow other processing
            self.sleep_func(0.1)

        # Process completed
        stdout_data = "".join(self.stdout_lines)
        stderr_data = "".join(self.stderr_lines)

        return {
            "success": self.process.returncode == 0,
            "exit_code": self.process.returncode,
            "stdout": stdout_data,
            "stderr": stderr_data,
        }
