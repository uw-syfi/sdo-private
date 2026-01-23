import json
import os
import signal
import subprocess
import sys
import threading
from typing import Optional, List

from .cli_agent import CLICodingAgent
from .opencode_events import OpencodeEvent, TextEvent


class OpencodeCodingAgent(CLICodingAgent):
    """Coding agent implementation using the Opencode CLI tool."""

    def __init__(self, model: Optional[str] = None):
        """Initialize the Opencode coding agent.

        Args:
            model: Optional model name to use.
        """
        if not model:
            model = "google-vertex/gemini-3-pro-preview"
        super().__init__("opencode", model)

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return "[Opencode]"

    def _get_command(self, prompt: str) -> List[str]:
        cmd = [self.binary_path, "run", f'"{prompt}"']

        if self.model:
            cmd.extend(["--model", self.model])

        # Output in json format
        cmd.extend(["--format=json"])

        return cmd

    def generate(
        self,
        prompt: str,
        cwd: Optional[str] = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        """Generate text using the Opencode CLI tool with stream-json parsing.

        Args:
            prompt: The prompt to send.
            cwd: Optional working directory.
            timeout: Timeout in seconds (default: 300).
            silent: If True, suppress stdout printing of the agent's output.

        Returns:
            Generated text (accumulated assistant content).
        """
        cmd = self._get_command(prompt)

        if not silent:
            print(f"{self._log_prefix} Running command: {' '.join(cmd)}")
            print("=" * 80)
            sys.stdout.flush()

        # Buffers to capture output
        accumulated_content = []
        stderr_lines = []

        # State for stream printing
        self._at_line_start = True

        def read_stdout(pipe):
            """Read stdout line by line, parse JSON, and print prettily."""
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                try:
                    data = json.loads(line)
                    event = OpencodeEvent.from_dict(data)
                    if event:
                        self._handle_event(event, silent, accumulated_content)
                except json.JSONDecodeError:
                    # Fallback for non-JSON lines
                    if not silent:
                        if self._at_line_start:
                            sys.stdout.write(f"{self._log_prefix} ")
                        sys.stdout.write(line.rstrip() + "\n")
                        self._at_line_start = True
                        sys.stdout.flush()
            pipe.close()

        def read_stderr(pipe):
            """Read stderr line by line and print + capture."""
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                if not silent:
                    # Ensure we are on a new line for stderr
                    if not self._at_line_start:
                        sys.stdout.write("\n")
                        self._at_line_start = True

                    print(
                        f"{self._log_prefix} [STDERR] {line.rstrip()}", file=sys.stderr
                    )
                    sys.stdout.flush()
                stderr_lines.append(line)
            pipe.close()

        # Run process
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            cwd=cwd,
            env=self.env,
            start_new_session=True,
        )

        # Start threads
        stdout_thread = threading.Thread(
            target=read_stdout, args=(process.stdout,))
        stderr_thread = threading.Thread(
            target=read_stderr, args=(process.stderr,))

        stdout_thread.daemon = True
        stderr_thread.daemon = True

        stdout_thread.start()
        stderr_thread.start()

        # Send prompt
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except BrokenPipeError:
            pass

        # Wait for process
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise subprocess.TimeoutExpired(cmd, timeout)
        finally:
            # Ensure process is killed on any exit (e.g. KeyboardInterrupt)
            if process.poll() is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    process.wait()
                except (ProcessLookupError, OSError):
                    pass

        # Wait for threads
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        stderr_data = "".join(stderr_lines)

        if not silent:
            if not self._at_line_start:
                print()  # Finish the last line
            print("=" * 80)

        if process.returncode != 0:
            raise RuntimeError(
                f"{self.binary_name} exited with code {process.returncode}: {stderr_data}"
            )

        return "".join(accumulated_content)

    def _handle_event(
        self,
        event: OpencodeEvent,
        silent: bool,
        accumulator: List[str],
    ):
        """Handle a single parsed Opencode event."""
        # 1. Update State
        if isinstance(event, TextEvent):
            accumulator.append(event.text)

        # 2. Render Output
        if not silent:
            self._render_event(event)

    def _render_event(self, event: OpencodeEvent):
        """Render the event to stdout."""
        # Handle streaming text differently from block events
        if isinstance(event, TextEvent):
            self._print_stream_content(event.text)
            return

        # Ensure we start block events on a new line
        if not self._at_line_start:
            sys.stdout.write("\n")
            self._at_line_start = True

        # Render and print
        output = event.render(self._log_prefix)
        if output:
            print(output)
            sys.stdout.flush()

    def _print_stream_content(self, content: str):
        """Print streaming content with prefix handling."""
        if not content:
            return

        lines = content.split("\n")

        for i, line in enumerate(lines):
            is_last = i == len(lines) - 1

            if is_last:
                if line:
                    if self._at_line_start:
                        sys.stdout.write(f"{self._log_prefix} ")
                        self._at_line_start = False
                    sys.stdout.write(line)
            else:
                if self._at_line_start:
                    sys.stdout.write(f"{self._log_prefix} ")
                sys.stdout.write(line)
                sys.stdout.write("\n")
                self._at_line_start = True

        sys.stdout.flush()
