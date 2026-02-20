import os
import shutil
import signal
import subprocess
import sys
import threading
from abc import abstractmethod
from typing import Optional, List

from app_operator.logger import logger
from .base import CodingAgent
from .utils import _get_interactive_env
from .events import AgentEventHandler
from app_operator.trajectory import TrajectoryRecorderProtocol, NullTrajectoryRecorder


class CLIGenerationSession:
    """Handles a single generation request lifecycle."""

    def __init__(
        self,
        binary_name: str,
        env: dict,
        log_prefix: str,
        cmd: List[str],
        logger,
        cwd: Optional[str] = None,
        timeout: int = 300,
        silent: bool = False,
        recorder: Optional[TrajectoryRecorderProtocol] = None,
        event_handler: Optional[AgentEventHandler] = None,
    ):
        self.binary_name = binary_name
        self.env = env
        self.log_prefix = log_prefix
        self.cmd = cmd
        self.logger = logger
        self.cwd = cwd
        self.timeout = timeout
        self.silent = silent
        self.recorder = recorder or NullTrajectoryRecorder()
        self.event_handler = event_handler

        # State initialization
        self.stdout_lines = []
        self.stderr_lines = []

    def _log_raw(self, message: str) -> None:
        """Log a raw message directly to output if not silent."""
        if not self.silent:
            self.logger.opt(raw=True).info(message)

    def _process_stdout(self, line: str) -> None:
        """Process a line from stdout."""
        line_stripped = line.rstrip("\n")
        if not self.silent:
            self.logger.info(line_stripped)

        if self.event_handler and line_stripped:
            self.event_handler.on_thinking(line_stripped + "\n")

        self.stdout_lines.append(line)

    def _process_stderr(self, line: str) -> None:
        """Process a line from stderr."""
        line_stripped = line.rstrip("\n")
        if not self.silent:
            self.logger.bind(stderr=True).info(f"[STDERR] {line_stripped}")
        self.stderr_lines.append(line)

    def run(self, prompt: str) -> str:
        """Execute the generation process."""
        if not self.silent:
            self.logger.info(f"Running command: {' '.join(self.cmd)}")
            self._log_raw("=" * 80 + "\n")
            sys.stdout.flush()

        def read_stdout(pipe):
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                self._process_stdout(line)
            pipe.close()

        def read_stderr(pipe):
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                self._process_stderr(line)
            pipe.close()

        process = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=self.cwd,
            env=self.env,
            start_new_session=True,
        )

        stdout_thread = threading.Thread(target=read_stdout, args=(process.stdout,))
        stderr_thread = threading.Thread(target=read_stderr, args=(process.stderr,))

        stdout_thread.daemon = True
        stderr_thread.daemon = True

        stdout_thread.start()
        stderr_thread.start()

        try:
            if process.stdin:
                process.stdin.write(prompt)
                process.stdin.close()
        except BrokenPipeError:
            pass

        try:
            process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise subprocess.TimeoutExpired(self.cmd, self.timeout)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    process.wait()
                except (ProcessLookupError, OSError):
                    pass

        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        stdout_data = "".join(self.stdout_lines)
        stderr_data = "".join(self.stderr_lines)

        self._log_raw("=" * 80 + "\n")

        if process.returncode != 0:
            raise RuntimeError(
                f"{self.binary_name} exited with code {process.returncode}: {stderr_data}"
            )

        return stdout_data.strip()


class CLICodingAgent(CodingAgent):
    """Base class for CLI-based coding agents."""

    def __init__(
        self,
        binary_name: str,
        model: Optional[str] = None,
        recorder: Optional[TrajectoryRecorderProtocol] = None,
        event_handler: Optional[AgentEventHandler] = None,
    ):
        """Initialize the CLI coding agent.

        Args:
            binary_name: The name of the executable to use.
            model: Optional model name to use.
            recorder: Trajectory recorder instance.
            event_handler: Optional event handler for UI updates.

        Raises:
            RuntimeError: If binary is not found in PATH or is not working.
        """
        self.env = _get_interactive_env()
        self.binary_name = binary_name
        self.model = model
        self.recorder = recorder or NullTrajectoryRecorder()
        self.event_handler = event_handler

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
        self.logger = logger.bind(agent_prefix=self._log_prefix)

    def _check_cli(self):
        """Check if the CLI tool is available and executable."""
        try:
            result = subprocess.run(
                [self.binary_path, "--help"],
                capture_output=True,
                text=True,
                check=False,
                env=self.env,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"{self.binary_name} CLI tool at '{self.binary_path}' is not working correctly. "
                    f"'{self.binary_path} --help' exited with code {result.returncode}. "
                    f"Stderr: {result.stderr}"
                )
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

    def _create_session(
        self,
        cmd: List[str],
        cwd: Optional[str] = None,
        timeout: int = 300,
        silent: bool = False,
        recorder: Optional[TrajectoryRecorderProtocol] = None,
    ) -> CLIGenerationSession:
        """Create a session for a single generation request.

        Can be overridden by subclasses to return specialized sessions.
        """
        return CLIGenerationSession(
            binary_name=self.binary_name,
            env=self.env,
            log_prefix=self._log_prefix,
            cmd=cmd,
            logger=self.logger,
            cwd=cwd,
            timeout=timeout,
            silent=silent,
            recorder=recorder,
            event_handler=self.event_handler,
        )

    def generate(
        self,
        prompt: str,
        cwd: Optional[str] = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        """Generate text using the CLI tool.

        Args:
            prompt: The prompt to send.
            cwd: Optional working directory.
            timeout: Timeout in seconds (default: 300).
            silent: If True, suppress stdout printing of the agent's output.

        Returns:
            Generated text.
        """
        # Record the prompt in trajectory
        self.recorder.add_user_message(prompt)

        cmd = self._get_command(prompt)
        session = self._create_session(
            cmd, cwd, timeout, silent, recorder=self.recorder
        )
        result = session.run(prompt)

        self.recorder.add_assistant_message(result)
        return result
