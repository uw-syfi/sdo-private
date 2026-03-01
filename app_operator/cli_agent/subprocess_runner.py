"""Subprocess runner with threading, timeout handling, and progress monitoring."""

import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from app_operator.logger import logger
from app_operator.ui import OperatorUI
from app_operator.types import CommandResult


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
        command: list[str],
        cwd: str,
        timeout: int,
        log_file_path: Path | None = None,
        check_shutdown: Callable[[], bool] | None = None,
        time_func: Callable[[], float] | None = None,
        sleep_func: Callable[[float], None] | None = None,
        popen_func: Callable | None = None,
        ui: OperatorUI | None = None,
        tool_name: str | None = None,
        tool_args: dict | str | None = None,
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
            ui: Optional UI for tool events.
            tool_name: Optional tool name for UI events.
            tool_args: Optional tool arguments for UI events.
        """
        self.command = command
        self.cwd = cwd
        self.timeout = timeout
        self.log_file_path = log_file_path
        self.check_shutdown = check_shutdown
        self.time_func = time_func if time_func is not None else time.time
        self.sleep_func = sleep_func if sleep_func is not None else time.sleep
        self.popen_func = popen_func if popen_func is not None else subprocess.Popen
        self.ui = ui
        self.tool_name = tool_name
        self.tool_args = tool_args

        self.process: subprocess.Popen | None = None
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self._log_file = None
        self._log_lock = threading.Lock()

    def run(self) -> CommandResult:
        """Run the subprocess and return results.

        Returns:
            CommandResult with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        return self._run_impl(self._wait_for_completion)

    def run_with_progress_monitoring(self, summarizer) -> CommandResult:
        """Run subprocess with integrated progress monitoring.

        This method starts the subprocess and monitors it, providing periodic
        progress summaries via the given summarizer.

        Args:
            summarizer: ProgressSummarizer instance for generating summaries.

        Returns:
            CommandResult with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        def progress_callback():
            if summarizer.should_summarize():
                recent_output = self.get_recent_output(num_lines=20)
                summarizer.summarize(recent_output)

        def wait_fn():
            return self._wait_for_completion(progress_callback=progress_callback,
                                             summarizer=summarizer)

        return self._run_impl(wait_fn)

    def _run_impl(
            self, wait_fn: Callable[[], CommandResult]) -> CommandResult:
        """Shared implementation for run() and run_with_progress_monitoring().

        Args:
            wait_fn: Callable that waits for process completion and returns a result dict.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
        """
        if self.ui and self.tool_name:
            self.ui.on_tool_call(self.tool_name, self.tool_args)

        start_time_mono = self.time_func()

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
                target=self._read_pipe, args=(
                    self.process.stdout, self.stdout_lines)
            )
            stderr_thread = threading.Thread(
                target=self._read_pipe, args=(
                    self.process.stderr, self.stderr_lines)
            )

            stdout_thread.daemon = True
            stderr_thread.daemon = True

            stdout_thread.start()
            stderr_thread.start()

            # Wait for process to complete or timeout/shutdown
            result = wait_fn()

            # Wait for threads to finish reading
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

            # Update stdout/stderr with full content captured by threads
            result["stdout"] = "".join(self.stdout_lines)
            # Append captured stderr to any existing error message (e.g.
            # timeout msg)
            result["stderr"] = result.get(
                "stderr", "") + "".join(self.stderr_lines)

            if self.ui and self.tool_name:
                duration = self.time_func() - start_time_mono
                self.ui.on_tool_result(
                    tool=self.tool_name,
                    stdout=result.get("stdout", ""),
                    stderr=result.get("stderr", ""),
                    exit_code=result.get("exit_code"),
                    duration=duration,
                )

            return result

        except (OSError, subprocess.SubprocessError) as e:
            result: CommandResult = {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Failed to run deployment script: {e}",
            }
            if self.ui and self.tool_name:
                self.ui.on_tool_result(
                    tool=self.tool_name,
                    stdout="",
                    stderr=result["stderr"],
                    exit_code=-1,
                )
            return result
        except Exception as e:
            result: CommandResult = {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Unexpected error running deployment script: {e}",
            }
            if self.ui and self.tool_name:
                self.ui.on_tool_result(
                    tool=self.tool_name,
                    stdout="",
                    stderr=result["stderr"],
                    exit_code=-1,
                )
            return result
        finally:
            if self._log_file:
                self._log_file.close()
            self._ensure_process_terminated()

    def _read_pipe(self, pipe, buffer: list[str]):
        """Read pipe line by line and capture to buffer.

        Args:
            pipe: The pipe to read from (stdout or stderr).
            buffer: list to append lines to.
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

    def _wait_for_completion(
        self,
        progress_callback: Callable[[], None] | None = None,
        summarizer=None,
    ) -> CommandResult:
        """Wait for process completion with timeout and shutdown checks.

        Args:
            progress_callback: Optional callable invoked each polling iteration
                for progress monitoring (e.g. summarizer checks).
            summarizer: Optional ProgressSummarizer. When provided, its start()
                method is called and debug logging is enabled.

        Returns:
            dict: Result with keys 'success', 'exit_code', 'stdout', 'stderr'.
            Note: stdout/stderr returned here only contain system messages.
            Captured output is in self.*_lines and merged in _run_impl().
        """
        if self.process is None:
            raise RuntimeError(
                "process is not initialized; call start() before using this method"
            )
        start_time = self.time_func()

        if summarizer is not None:
            summarizer.start(start_time=start_time)
            logger.debug(
                f"SubprocessRunner: Started monitoring with summarizer "
                f"(initial_delay={
                    summarizer.initial_delay}s, interval={
                    summarizer.summary_interval}s)"
            )

        # Handle timeout=0 as a special case that fails immediately
        if self.timeout == 0:
            self._ensure_process_terminated()
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "Deployment script timed out after 0 seconds\n",
            }

        loop_iterations = 0
        while True:
            current_time = self.time_func()
            elapsed = current_time - start_time
            loop_iterations += 1

            # Log monitoring progress every 10 seconds (100 iterations at 0.1s
            # sleep)
            if summarizer is not None and loop_iterations % 100 == 0:
                logger.debug(
                    f"SubprocessRunner: Monitoring loop iter={loop_iterations}, "
                    f"elapsed={
                        elapsed:.1f}s, process_running={
                        self.process.poll() is None}"
                )

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
                        "stdout": "",
                        "stderr": f"Deployment script timed out after {self.timeout} seconds\n",
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
                    "stdout": "",
                    "stderr": "Deployment interrupted by shutdown request\n",
                }

            # Check for progress summary if callback provided
            if progress_callback is not None:
                progress_callback()

            # Yield to allow other processing
            self.sleep_func(0.1)

        # Process completed
        return {
            "success": self.process.returncode == 0,
            "exit_code": self.process.returncode,
            "stdout": "",
            "stderr": "",
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
