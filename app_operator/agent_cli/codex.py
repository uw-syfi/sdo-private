import os
import shutil
import subprocess
import sys
import threading
from typing import Optional

from .base import CodingAgent
from .utils import _get_interactive_env


class CodexCodingAgent(CodingAgent):
    """Coding agent implementation using the Codex CLI tool."""

    def __init__(self, model: Optional[str] = None):
        """Initialize the Codex coding agent.

        Args:
            model: Optional model name to use with codex. If None, uses default.

        Raises:
            RuntimeError: If codex binary is not found in PATH or is not working.
        """
        self.env = _get_interactive_env()

        # Search for codex in the captured environment's PATH
        codex_path = shutil.which("codex", path=self.env.get("PATH"))

        if not codex_path:
            # Fallback to current PATH if not found in interactive env
            codex_path = shutil.which("codex")

        if not codex_path:
            raise RuntimeError(
                "codex binary not found in PATH. "
                "Please ensure codex is installed and available."
            )
        self.codex_path = codex_path
        self.model = model
        self._check_cli()

    def _check_cli(self):
        """Check if the codex CLI tool is available and executable."""
        try:
            result = subprocess.run(
                [self.codex_path, "--help"],
                capture_output=True,
                text=True,
                check=False,
                env=self.env
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Codex CLI tool at '{self.codex_path}' is not working correctly. "
                    f"'{self.codex_path} --help' exited with code {result.returncode}. "
                    f"Stderr: {result.stderr}")
        except FileNotFoundError:
            raise RuntimeError(
                f"Codex CLI tool not found at '{self.codex_path}'. "
                "Please ensure codex is installed and in your PATH."
            )
        except Exception as e:
            raise RuntimeError(f"Failed to check Codex CLI tool: {e}")

    def generate(self, prompt: str,
                 cwd: Optional[str] = None, timeout: int = 300, silent: bool = False) -> str:
        """Generate text using codex.

        Args:
            prompt: The prompt to send to codex.
            cwd: Optional working directory to run codex in. If None, uses current directory.
            timeout: Timeout in seconds (default: 300).
            silent: If True, suppress stdout printing of the agent's output.

        Returns:
            Generated text from codex.

        Raises:
            RuntimeError: If codex execution fails.
            subprocess.TimeoutExpired: If codex times out.
        """
        # Prepare codex command
        # "exec" to run in non-interactive mode
        cmd = [
            self.codex_path,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox"
        ]
        if self.model:
            cmd.extend(["--model", self.model])

        if not silent:
            print(f"[CodexCodingAgent] Running command: {' '.join(cmd)}")
            print(f"[CodexCodingAgent] Working directory: {cwd or os.getcwd()}")
            print(f"[CodexCodingAgent] Prompt length: {len(prompt)} characters")
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
                    print(f"[CodexCodingAgent] {line_stripped}")
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
                        f"[CodexCodingAgent] [STDERR] {line_stripped}",
                        file=sys.stderr)
                    sys.stderr.flush()
                buffer.append(line)
            pipe.close()

        # Run codex with Popen to capture and print output in real-time
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

        if not silent:
            print("=" * 80)

        if process.returncode != 0:
            raise RuntimeError(
                f"codex exited with code {process.returncode}: {stderr_data}"
            )

        return stdout_data.strip()
