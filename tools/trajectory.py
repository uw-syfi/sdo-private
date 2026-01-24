"""
Trajectory capture module for SDS.

This module provides real-time trajectory capture during SDS execution,
recording all agent interactions, prompts, responses, and tool calls
into a structured trajectory.json file.
"""

import json
import shutil
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional
from enum import Enum


# Maximum characters to capture in tool output (stdout + stderr combined)
MAX_OUTPUT_LENGTH = 10000

# Gemini CLI session storage location
GEMINI_SESSION_DIR = Path.home() / ".gemini" / "tmp"


class Phase(str, Enum):
    """Phases of SDS operation."""
    SCRIPT_GENERATION = "script_generation"
    DEPLOYMENT = "deployment"
    MONITORING = "monitoring"


class MessageRole(str, Enum):
    """Role of a message in the trajectory."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALL = "tool_call"


@dataclass
class TrajectoryMessage:
    """A single message in the trajectory."""
    role: str
    content: Optional[str] = None
    tool: Optional[str] = None
    args: Optional[Dict[str, Any]] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    timestamp: Optional[str] = None
    duration_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        d = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool is not None:
            d["tool"] = self.tool
        if self.args is not None:
            d["args"] = self.args
        if self.stdout is not None:
            d["stdout"] = self.stdout
        if self.stderr is not None:
            d["stderr"] = self.stderr
        if self.exit_code is not None:
            d["exit_code"] = self.exit_code
        if self.timestamp is not None:
            d["timestamp"] = self.timestamp
        if self.duration_seconds is not None:
            d["duration_seconds"] = self.duration_seconds
        return d


class TrajectoryRecorder:
    """Records agent interactions in real-time during SDS execution.

    This is a singleton class that can be accessed globally to record
    trajectory data from any part of the SDS codebase.
    """

    _instance: Optional['TrajectoryRecorder'] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Ensure only one instance exists."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, repo_path: Optional[Path] = None):
        """Initialize the trajectory recorder.

        Args:
            repo_path: Path to the repository. Required on first initialization.
        """
        if self._initialized and repo_path is None:
            return

        if repo_path is not None:
            self.repo_path = Path(repo_path)
            self.sds_dir = self.repo_path / ".sds"
            self.trajectories_dir = self.sds_dir / "trajectories"

            # Create timestamped filename for this run
            self._run_timestamp = time.strftime("%Y%m%d-%H%M%S")
            self.trajectory_file = self.trajectories_dir / f"trajectory_{self._run_timestamp}.json"

            # Also maintain a symlink to the latest trajectory
            self._latest_link = self.sds_dir / "trajectory.json"

            # Initialize trajectory structure
            self.trajectory: Dict[str, Any] = {
                "metadata": {
                    "repo_path": str(self.repo_path),
                    "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "end_time": None,
                    "agent_name": None,
                    "status": "running"
                },
                "script_generation": [],
                "deployment": [],
                "monitoring": [],
                "gemini_sessions": []  # Will store paths to gemini session files
            }

            # Current conversation being recorded (not yet committed)
            self._current_phase: Optional[Phase] = None
            self._current_conversation: List[Dict[str, Any]] = []
            self._conversation_lock = threading.Lock()

            # Ensure directories exist
            self.sds_dir.mkdir(parents=True, exist_ok=True)
            self.trajectories_dir.mkdir(parents=True, exist_ok=True)

            # Save initial state
            self._write_to_file()

            self._initialized = True

    @classmethod
    def get_instance(cls) -> Optional['TrajectoryRecorder']:
        """Get the current instance if it exists."""
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset the singleton instance."""
        with cls._lock:
            cls._instance = None

    def set_agent_name(self, agent_name: str) -> None:
        """Set the agent name in metadata."""
        self.trajectory["metadata"]["agent_name"] = agent_name
        self._write_to_file()

    def start_phase(self, phase: Phase, context: Dict[str, Any] = None) -> None:
        """Start a new phase/conversation.

        Args:
            phase: The phase type (script_generation, deployment, monitoring).
            context: Additional context for the phase (e.g., attempt number).
        """
        with self._conversation_lock:
            # Commit any existing conversation first
            self._commit_current_conversation()

            # Start new conversation
            self._current_phase = phase
            self._current_conversation = []

            # Add system message with context
            system_content = self._get_system_prompt(phase, context)
            self._current_conversation.append(
                TrajectoryMessage(
                    role=MessageRole.SYSTEM.value,
                    content=system_content,
                    timestamp=time.strftime("%Y-%m-%d %H:%M:%S")
                ).to_dict()
            )

    def _get_system_prompt(self, phase: Phase, context: Dict[str, Any] = None) -> str:
        """Generate system prompt for a phase."""
        context = context or {}
        agent_name = self.trajectory["metadata"].get("agent_name", "AI Agent")

        if phase == Phase.SCRIPT_GENERATION:
            return (
                f"You are an AI operator ({agent_name}) responsible for analyzing "
                f"the repository and generating deployment scripts (deploy.sh) and "
                f"health check scripts (health_check.sh)."
            )
        elif phase == Phase.DEPLOYMENT:
            attempt = context.get("attempt", 1)
            max_attempts = context.get("max_attempts", 5)
            return (
                f"You are an AI operator ({agent_name}) responsible for deploying "
                f"the application and fixing any deployment errors. "
                f"Deployment attempt {attempt} of {max_attempts}."
            )
        elif phase == Phase.MONITORING:
            cycle = context.get("cycle", 1)
            return (
                f"You are an AI operator ({agent_name}) responsible for analyzing "
                f"application health check results and providing recommendations. "
                f"Monitoring cycle #{cycle}."
            )
        return f"You are an AI operator ({agent_name})."

    def add_user_message(self, content: str) -> None:
        """Add a user/prompt message to the current conversation."""
        with self._conversation_lock:
            if self._current_phase is not None:
                self._current_conversation.append(
                    TrajectoryMessage(
                        role=MessageRole.USER.value,
                        content=content,
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S")
                    ).to_dict()
                )

    def add_assistant_message(self, content: str, duration: float = None) -> None:
        """Add an assistant response to the current conversation."""
        with self._conversation_lock:
            if self._current_phase is not None:
                self._current_conversation.append(
                    TrajectoryMessage(
                        role=MessageRole.ASSISTANT.value,
                        content=content,
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                        duration_seconds=duration
                    ).to_dict()
                )

    def add_tool_call(
        self,
        tool: str,
        args: Dict[str, Any],
        stdout: str = "",
        stderr: str = "",
        exit_code: int = None,
        duration: float = None
    ) -> None:
        """Add a tool call with its output to the current conversation.

        Args:
            tool: Name of the tool (e.g., "bash", "write_file")
            args: Arguments passed to the tool
            stdout: Standard output from the tool
            stderr: Standard error from the tool
            exit_code: Exit code from the tool (for bash commands)
            duration: How long the tool took to execute
        """
        with self._conversation_lock:
            if self._current_phase is not None:
                # Truncate outputs if too long, but keep both stdout and stderr
                truncated_stdout = stdout
                truncated_stderr = stderr

                if len(stdout) + len(stderr) > MAX_OUTPUT_LENGTH:
                    # Allocate space proportionally, but ensure stderr gets captured
                    stderr_limit = min(len(stderr), MAX_OUTPUT_LENGTH // 3)
                    stdout_limit = MAX_OUTPUT_LENGTH - stderr_limit

                    if len(stdout) > stdout_limit:
                        truncated_stdout = f"[truncated, showing last {stdout_limit} chars]\n..." + \
                            stdout[-stdout_limit:]
                    if len(stderr) > stderr_limit:
                        truncated_stderr = f"[truncated, showing last {stderr_limit} chars]\n..." + \
                            stderr[-stderr_limit:]

                self._current_conversation.append(
                    TrajectoryMessage(
                        role=MessageRole.TOOL_CALL.value,
                        tool=tool,
                        args=args,
                        stdout=truncated_stdout if truncated_stdout else None,
                        stderr=truncated_stderr if truncated_stderr else None,
                        exit_code=exit_code,
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                        duration_seconds=duration
                    ).to_dict()
                )

    def end_phase(self, status: str = None) -> None:
        """End the current phase, commit conversation, and save to file."""
        with self._conversation_lock:
            if self._current_phase is None:
                return

            # Add final status message
            if status:
                self._current_conversation.append(
                    TrajectoryMessage(
                        role=MessageRole.ASSISTANT.value,
                        content=f"Phase completed with status: {status}",
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S")
                    ).to_dict()
                )

            # Commit the conversation to trajectory
            self._commit_current_conversation()

            # Reset current state
            self._current_phase = None
            self._current_conversation = []

        # Save to file (outside lock to avoid holding it during I/O)
        self._write_to_file()

    def _commit_current_conversation(self) -> None:
        """Commit the current conversation to the trajectory structure.

        Must be called while holding _conversation_lock.
        """
        if self._current_phase and self._current_conversation:
            phase_key = self._current_phase.value
            if phase_key in self.trajectory:
                # Append a copy of the conversation
                self.trajectory[phase_key].append(self._current_conversation.copy())

    def _write_to_file(self) -> None:
        """Write the current trajectory state to file."""
        try:
            # Update end time
            self.trajectory["metadata"]["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")

            # Ensure directories exist
            self.trajectories_dir.mkdir(parents=True, exist_ok=True)

            with open(self.trajectory_file, "w") as f:
                json.dump(self.trajectory, f, indent=2)

            # Update symlink to latest trajectory
            self._update_latest_link()
        except Exception as e:
            # Log error but don't crash
            print(f"Warning: Failed to write trajectory file: {e}")

    def _update_latest_link(self) -> None:
        """Update the trajectory.json symlink to point to the latest trajectory."""
        try:
            # Remove existing symlink or file
            if self._latest_link.exists() or self._latest_link.is_symlink():
                self._latest_link.unlink()

            # Create relative symlink
            rel_path = self.trajectory_file.relative_to(self.sds_dir)
            self._latest_link.symlink_to(rel_path)
        except Exception:
            # If symlink fails (e.g., on Windows), just copy the file
            try:
                shutil.copy2(self.trajectory_file, self._latest_link)
            except Exception:
                pass

    def save(self) -> Path:
        """Save the trajectory to file (alias for _write_to_file).

        Returns:
            Path to the trajectory file.
        """
        self._write_to_file()
        return self.trajectory_file

    def _collect_gemini_sessions(self) -> None:
        """Collect and copy recent Gemini CLI session files to the trajectory directory."""
        if not GEMINI_SESSION_DIR.exists():
            return

        try:
            # Get the run start time
            start_time_str = self.trajectory["metadata"]["start_time"]
            # Parse as struct_time for comparison
            run_start = time.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
            run_start_ts = time.mktime(run_start)

            # Find all session files created after our run started
            gemini_sessions_dir = self.trajectories_dir / "gemini_sessions" / self._run_timestamp
            sessions_copied = []

            for project_dir in GEMINI_SESSION_DIR.iterdir():
                if not project_dir.is_dir() or project_dir.name == "bin":
                    continue

                chats_dir = project_dir / "chats"
                if not chats_dir.exists():
                    continue

                for session_file in chats_dir.glob("session-*.json"):
                    # Check if file was modified after run started
                    file_mtime = session_file.stat().st_mtime
                    if file_mtime >= run_start_ts:
                        # Copy session file to our trajectory directory
                        gemini_sessions_dir.mkdir(parents=True, exist_ok=True)
                        dest_file = gemini_sessions_dir / f"{project_dir.name}_{session_file.name}"
                        shutil.copy2(session_file, dest_file)
                        sessions_copied.append(str(dest_file.relative_to(self.sds_dir)))

            # Record copied session paths in trajectory
            if sessions_copied:
                self.trajectory["gemini_sessions"] = sessions_copied

        except Exception as e:
            print(f"Warning: Failed to collect Gemini sessions: {e}")

    def finalize(self, status: str = "completed") -> Path:
        """Finalize the trajectory recording.

        Args:
            status: Final status (completed, failed, interrupted).

        Returns:
            Path to the saved trajectory file.
        """
        with self._conversation_lock:
            # Commit any in-progress conversation
            if self._current_phase:
                self._current_conversation.append(
                    TrajectoryMessage(
                        role=MessageRole.ASSISTANT.value,
                        content=f"Run ended with status: {status}",
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S")
                    ).to_dict()
                )
                self._commit_current_conversation()
                self._current_phase = None
                self._current_conversation = []

        # Collect Gemini CLI session files
        self._collect_gemini_sessions()

        self.trajectory["metadata"]["status"] = status
        self._write_to_file()
        return self.trajectory_file


# Global convenience functions for easy access from anywhere in the codebase

def init_trajectory(repo_path: Path) -> TrajectoryRecorder:
    """Initialize the global trajectory recorder.

    Args:
        repo_path: Path to the repository.

    Returns:
        The initialized TrajectoryRecorder instance.
    """
    TrajectoryRecorder.reset()
    return TrajectoryRecorder(repo_path)


def get_trajectory() -> Optional[TrajectoryRecorder]:
    """Get the current trajectory recorder instance."""
    return TrajectoryRecorder.get_instance()


def record_phase_start(phase: Phase, context: Dict[str, Any] = None) -> None:
    """Start recording a new phase."""
    recorder = get_trajectory()
    if recorder:
        recorder.start_phase(phase, context)


def record_user_message(content: str) -> None:
    """Record a user/prompt message."""
    recorder = get_trajectory()
    if recorder:
        recorder.add_user_message(content)


def record_assistant_message(content: str, duration: float = None) -> None:
    """Record an assistant response."""
    recorder = get_trajectory()
    if recorder:
        recorder.add_assistant_message(content, duration)


def record_tool_call(
    tool: str,
    args: Dict[str, Any],
    stdout: str = "",
    stderr: str = "",
    exit_code: int = None,
    duration: float = None
) -> None:
    """Record a tool call with its output."""
    recorder = get_trajectory()
    if recorder:
        recorder.add_tool_call(tool, args, stdout, stderr, exit_code, duration)


def record_phase_end(status: str = None) -> None:
    """End the current phase."""
    recorder = get_trajectory()
    if recorder:
        recorder.end_phase(status)


def finalize_trajectory(status: str = "completed") -> Optional[Path]:
    """Finalize and save the trajectory."""
    recorder = get_trajectory()
    if recorder:
        return recorder.finalize(status)
    return None
