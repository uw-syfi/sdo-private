import os
import shutil
import signal
import subprocess
import sys
import threading
from abc import abstractmethod
from typing import Optional, List

from .base import CodingAgent
from .utils import _get_interactive_env


class CLICodingAgent(CodingAgent):
    """Base class for CLI-based coding agents."""

    def __init__(self, binary_name: str, model: Optional[str] = None):
        """Initialize the CLI coding agent.

        Args:
            binary_name: The name of the executable to use.
            model: Optional model name to use.

        Raises:
            RuntimeError: If binary is not found in PATH or is not working.
        """
        self.env = _get_interactive_env()
        self.binary_name = binary_name
        self.model = model

        # Search for binary in the captured environment's PATH
        binary_path = shutil.which(binary_name, path=self.env.get("PATH"))

        if not binary_path:
            # Fallback to current PATH if not found in interactive env
            binary_path = shutil.which(binary_name)

        if not binary_path:
            raise RuntimeError(
                f"{binary_name} binary not found in PATH. "
                f"Please ensure {binary_name} is installed and available."
            )
        self.binary_path = binary_path
        self._check_cli()

    def _check_cli(self):
        """Check if the CLI tool is available and executable."""
        try:
            result = subprocess.run(
                [self.binary_path, "--help"],
                capture_output=True,
                text=True,
                check=False,
                env=self.env
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"{self.binary_name} CLI tool at '{self.binary_path}' is not working correctly. "
                    f"'{self.binary_path} --help' exited with code {result.returncode}. "
                    f"Stderr: {result.stderr}")
        except FileNotFoundError:
            raise RuntimeError(
                f"{self.binary_name} CLI tool not found at '{self.binary_path}'. "
                f"Please ensure {self.binary_name} is installed and in your PATH."
            )
        except Exception as e:
            raise RuntimeError(f"Failed to check {self.binary_name} CLI tool: {e}")

    @abstractmethod
    def _get_command(self, prompt: str) -> List[str]:
        """Construct the command line arguments."""
        pass

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return f"[{self.__class__.__name__}]"

    def generate(self, prompt: str,
                 cwd: Optional[str] = None, timeout: int = 300, silent: bool = False) -> str:
        """Generate text using the CLI tool.

        Args:
            prompt: The prompt to send.
            cwd: Optional working directory.
            timeout: Timeout in seconds (default: 300).
            silent: If True, suppress stdout printing of the agent's output.

        Returns:
            Generated text.

        Raises:
            RuntimeError: If execution fails.
            subprocess.TimeoutExpired: If execution times out.
        """
        cmd = self._get_command(prompt)

        if not silent:
            # Only print extra details if verbose/debug is desired,
            # but matching original behavior which printed command details for Codex
            # and separator for Gemini.
            # We'll normalize to printing separator and command for both if feasible,
            # or keep it simple.
            print(f"{self._log_prefix} Running command: {' '.join(cmd)}")
            print("=" * 80)
            sys.stdout.flush()

        # Buffers to capture output
        stdout_lines = []
        stderr_lines = []

        def read_stdout(pipe, buffer):
            """Read stdout line by line and print + capture."""
            for line in iter(pipe.readline, ''):
                if not line:
                    break
                line_stripped = line.rstrip('\n')
                if not silent:
                    print(f"{self._log_prefix} {line_stripped}")
                    sys.stdout.flush()
                buffer.append(line)
            pipe.close()

        def read_stderr(pipe, buffer):
            """Read stderr line by line and print + capture."""
            for line in iter(pipe.readline, ''):
                if not line:
                    break
                line_stripped = line.rstrip('\n')
                if not silent:
                    print(
                        f"{self._log_prefix} [STDERR] {line_stripped}",
                        file=sys.stderr)
                    sys.stderr.flush()
                buffer.append(line)
            pipe.close()

        # Run process with Popen to capture and print output in real-time
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            cwd=cwd,
            env=self.env,
            start_new_session=True
        )

        # Start threads to read stdout and stderr concurrently
        stdout_thread = threading.Thread(
            target=read_stdout, args=(
                process.stdout, stdout_lines))
        stderr_thread = threading.Thread(
            target=read_stderr, args=(
                process.stderr, stderr_lines))

        stdout_thread.daemon = True
        stderr_thread.daemon = True

        stdout_thread.start()
        stderr_thread.start()

        # Send the prompt to stdin and close it
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except BrokenPipeError:
            pass

        # Wait for process to complete with timeout
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the entire process group
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass  # Process might be already gone
            process.wait()
            raise subprocess.TimeoutExpired(cmd, timeout)

        # Wait for threads to finish reading
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        # Combine captured output
        stdout_data = ''.join(stdout_lines)
        stderr_data = ''.join(stderr_lines)

        if not silent:
            print("=" * 80)

        if process.returncode != 0:
            raise RuntimeError(
                f"{self.binary_name} exited with code {process.returncode}: {stderr_data}"
            )

        return stdout_data.strip()
