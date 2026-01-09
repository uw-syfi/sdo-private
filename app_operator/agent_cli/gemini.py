import shutil
import subprocess
import sys
import threading
from typing import Optional

from .base import CodingAgent
from .utils import _get_interactive_env


class GeminiCodingAgent(CodingAgent):
    """Coding agent implementation using the Gemini CLI tool."""

    def __init__(self, model: Optional[str] = None):
        """Initialize the Gemini coding agent.

        Args:
            model: Optional model name to use.

        Raises:
            RuntimeError: If gemini binary is not found in PATH or is not working.
        """
        self.env = _get_interactive_env()

        # Search for gemini in the captured environment's PATH
        gemini_path = shutil.which("gemini", path=self.env.get("PATH"))

        if not gemini_path:
            # Fallback to current PATH if not found in interactive env
            gemini_path = shutil.which("gemini")

        if not gemini_path:
            raise RuntimeError(
                "gemini binary not found in PATH. "
                "Please ensure gemini is installed and available."
            )
        self.gemini_path = gemini_path
        self.model = model
        self._check_cli()

    def _check_cli(self):
        """Check if the gemini CLI tool is available and executable."""
        try:
            result = subprocess.run(
                [self.gemini_path, "--help"],
                capture_output=True,
                text=True,
                check=False,
                env=self.env
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Gemini CLI tool at '{self.gemini_path}' is not working correctly. "
                    f"'{self.gemini_path} --help' exited with code {result.returncode}. "
                    f"Stderr: {result.stderr}")
        except FileNotFoundError:
            raise RuntimeError(
                f"Gemini CLI tool not found at '{self.gemini_path}'. "
                "Please ensure gemini is installed and in your PATH."
            )
        except Exception as e:
            raise RuntimeError(f"Failed to check Gemini CLI tool: {e}")

    def generate(self, prompt: str,
                 cwd: Optional[str] = None, timeout: int = 300) -> str:
        """Generate text using gemini.

        Args:
            prompt: The prompt to send to gemini.
            cwd: Optional working directory to run gemini in.
            timeout: Timeout in seconds (default: 300).

        Returns:
            Generated text from gemini.
        """
        # Prepare gemini command
        cmd = [self.gemini_path]

        # Enable yolo mode
        cmd.extend(["-y"])

        if self.model:
            cmd.extend(["--model", self.model])

        print("-" * 80)
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
                print(f"[Gemini] {line_stripped}")
                sys.stdout.flush()
                buffer.append(line)
            pipe.close()

        def read_stderr(pipe, buffer):
            """Read stderr line by line and print + capture."""
            for line in iter(pipe.readline, ''):
                if not line:
                    break
                line_stripped = line.rstrip('\n')
                print(
                    f"[Gemini] [STDERR] {line_stripped}",
                    file=sys.stderr)
                sys.stderr.flush()
                buffer.append(line)
            pipe.close()

        # Run gemini with Popen to capture and print output in real-time
        # Pass the captured environment to the subprocess
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            cwd=cwd,
            env=self.env
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
            process.kill()
            process.wait()
            raise subprocess.TimeoutExpired(cmd, timeout)

        # Wait for threads to finish reading
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        # Combine captured output
        stdout_data = ''.join(stdout_lines)
        stderr_data = ''.join(stderr_lines)

        print("-" * 80)

        if process.returncode != 0:
            raise RuntimeError(
                f"gemini exited with code {process.returncode}: {stderr_data}"
            )

        print("[Gemini] Command completed successfully (exit code: 0)")
        print("=" * 80)
        sys.stdout.flush()

        return stdout_data.strip()
