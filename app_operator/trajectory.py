"""
Trajectory capture module for SDS.

This module provides real-time trajectory capture during SDS execution,
recording all agent interactions, prompts, responses, and tool calls
into a structured trajectory.json file.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
)

from app_operator.logger import logger
from app_operator.trajectory_collectors import collect_gemini_sessions

if TYPE_CHECKING:
    from app_operator.types import ConversationEntry, FaultInjectionMetadata, TokenUsage, TrajectoryCallRecord

from libs.agent_cli.trajectory import (
    NullTrajectoryRecorder,  # noqa: F401
    TrajectoryRecorderProtocol,  # noqa: F401
)
from libs.agent_cli.trajectory import (
    register_context_providers as _register_context_providers,
)

DEFAULT_MAX_OUTPUT_LENGTH = 10000  # characters captured per tool output


class Phase(str, Enum):
    """Phases of SDS operation."""

    EXPLORATION = "exploration"
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
    content: str | None = None
    tool: str | None = None
    args: dict[str, Any] | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    timestamp: str | None = None
    duration_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}


class TrajectoryRecorder:
    """Records agent interactions in real-time during SDS execution.

    Writes JSON to .sds/trajectories/
    """

    def __init__(self, repo_path: Path, max_output_length: int = DEFAULT_MAX_OUTPUT_LENGTH):
        """Initialize the trajectory recorder.

        Args:
            repo_path: Path to the repository.
            max_output_length: Maximum characters to capture in tool output.
        """
        self.repo_path = Path(repo_path)
        self.max_output_length = max_output_length
        self.sds_dir = self.repo_path / ".sds"
        self.trajectories_dir = self.sds_dir / "trajectories"

        # Create timestamped filename for this run
        self._run_timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.trajectory_file = self.trajectories_dir / f"trajectory_{self._run_timestamp}.json"

        # Also maintain a symlink to the latest trajectory
        self._latest_link = self.sds_dir / "trajectory.json"

        # Sequential call ID counter for tracking agent calls
        self._call_counter = 0
        self._call_id_lock = threading.Lock()

        # Initialize trajectory structure
        self.trajectory: dict[str, Any] = {
            "metadata": {
                "repo_path": str(self.repo_path),
                "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": None,
                "agent_name": None,
                "status": "running",
                "run_id": self._run_timestamp,
            },
            "calls": [],  # Sequential list of all agent calls with metadata
            "exploration": [],
            "script_generation": [],
            "deployment": [],
            "monitoring": [],
            "gemini_sessions": [],  # Will store paths to gemini session files
        }

        # Current conversation being recorded (not yet committed)
        self._current_phase: Phase | None = None
        self._current_conversation: list[dict] = []
        self._current_call_id: int | None = None
        self._conversation_lock = threading.Lock()

        # Pending status for the current phase (set by set_phase_status)
        self._pending_phase_status: str | None = None

        # Prompt version tracking for DSPy integration
        self._current_prompt_version: str | None = None
        self._current_prompt_kwargs: dict[str, Any] | None = None
        self._current_rendered_prompt: str | None = None
        self._fallback_occurred: bool = False

        # Prevent double finalization
        self._finalized = False

        # Ensure directories exist
        self.sds_dir.mkdir(parents=True, exist_ok=True)
        self.trajectories_dir.mkdir(parents=True, exist_ok=True)

        # Save initial state
        self._write_to_file()

    def set_agent_name(self, agent_name: str) -> None:
        """Set the agent name in metadata."""
        self.trajectory["metadata"]["agent_name"] = agent_name
        self._write_to_file()

    def get_current_call_id(self) -> int | None:
        """Get the current call ID for the active phase.

        Returns:
            Current call_id or None if no phase is active.
        """
        return self._current_call_id

    def get_run_id(self) -> str:
        """Get the unique run ID (timestamp) for this trajectory.

        Returns:
            Run ID string (timestamp format).
        """
        return self._run_timestamp

    def _get_next_call_id(self) -> int:
        """Get the next sequential call ID."""
        with self._call_id_lock:
            self._call_counter += 1
            return self._call_counter

    def start_phase(self, phase: Phase, context: dict[str, Any] | None = None) -> None:
        """Start a new phase/conversation.

        Args:
            phase: The phase type.
            context: Additional context for the phase.
        """
        with self._conversation_lock:
            # Commit any existing conversation first
            self._commit_current_conversation()

            # Generate a new sequential call ID for this phase
            self._current_call_id = self._get_next_call_id()

            # Start new conversation
            self._current_phase = phase
            self._current_conversation = []
            self._pending_phase_status = None

            # Record this call in the calls list
            call_start_time = time.strftime("%Y-%m-%d %H:%M:%S")
            call_record: TrajectoryCallRecord = {
                "call_id": self._current_call_id,
                "phase": phase.value,
                "start_time": call_start_time,
                "end_time": None,
                "context": context or {},
            }
            self.trajectory["calls"].append(call_record)

    def add_system_message(self, content: str) -> None:
        """Add a system prompt message to the current conversation."""
        with self._conversation_lock:
            if self._current_phase is not None:
                self._current_conversation.append(
                    TrajectoryMessage(
                        role=MessageRole.SYSTEM.value,
                        content=content,
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                    ).to_dict()
                )
            else:
                logger.warning("Attempted to record system message outside of a phase")

    def add_user_message(self, content: str) -> None:
        """Add a user/prompt message to the current conversation."""
        with self._conversation_lock:
            if self._current_phase is not None:
                self._current_conversation.append(
                    TrajectoryMessage(
                        role=MessageRole.USER.value,
                        content=content,
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                    ).to_dict()
                )
            else:
                logger.warning("Attempted to record user message outside of a phase")

    def add_assistant_message(self, content: str, duration: float | None = None) -> None:
        """Add an assistant response to the current conversation."""
        with self._conversation_lock:
            if self._current_phase is not None:
                self._current_conversation.append(
                    TrajectoryMessage(
                        role=MessageRole.ASSISTANT.value,
                        content=content,
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                        duration_seconds=duration,
                    ).to_dict()
                )
            else:
                logger.warning("Attempted to record assistant message outside of a phase")

    def add_tool_call(
        self,
        tool: str,
        args: dict[str, Any],
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        duration: float | None = None,
    ) -> None:
        """Add a tool call with its output to the current conversation."""
        with self._conversation_lock:
            if self._current_phase is not None:
                # Truncate outputs if too long, but keep both stdout and stderr
                truncated_stdout = stdout
                truncated_stderr = stderr

                if len(stdout) + len(stderr) > self.max_output_length:
                    # Allocate space proportionally, but ensure stderr gets captured
                    stderr_limit = min(len(stderr), self.max_output_length // 3)
                    stdout_limit = self.max_output_length - stderr_limit

                    if len(stdout) > stdout_limit:
                        truncated_stdout = (
                            f"[truncated, showing last {stdout_limit} chars]\n..." + stdout[-stdout_limit:]
                        )
                    if len(stderr) > stderr_limit:
                        truncated_stderr = (
                            f"[truncated, showing last {stderr_limit} chars]\n..." + stderr[-stderr_limit:]
                        )

                self._current_conversation.append(
                    TrajectoryMessage(
                        role=MessageRole.TOOL_CALL.value,
                        tool=tool,
                        args=args,
                        stdout=truncated_stdout or None,
                        stderr=truncated_stderr or None,
                        exit_code=exit_code,
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                        duration_seconds=duration,
                    ).to_dict()
                )
            else:
                logger.warning("Attempted to record tool call outside of a phase")

    def set_phase_status(self, status: str) -> None:
        """Set the status to be used when ending the current phase."""
        self._pending_phase_status = status

    def set_prompt_version(self, version: str) -> None:
        """Record which prompt version was used (jinja2 or dspy_vN).

        Args:
            version: Version identifier (e.g., 'jinja2', 'dspy_v1')
        """
        self._current_prompt_version = version

    def record_fallback(self) -> None:
        """Record that a fallback from DSPy to Jinja2 occurred."""
        self._fallback_occurred = True

    def record_prompt_kwargs(self, kwargs: dict[str, Any]) -> None:
        """Record the structured kwargs passed to a prompt render call.

        Filters out internal keys (starting with '_') and converts Path values
        to strings for JSON serialization.

        Args:
            kwargs: The keyword arguments passed to the prompt renderer.
        """
        filtered = {k: str(v) if isinstance(v, Path) else v for k, v in kwargs.items() if not k.startswith("_")}
        self._current_prompt_kwargs = filtered

    def record_rendered_prompt(self, rendered_prompt: str) -> None:
        """Record the rendered prompt string returned by the prompt renderer.

        This is the string sent to the coding agent as its instruction prompt.
        Stored in the trajectory as ground-truth output for DSPy optimization.

        Args:
            rendered_prompt: The rendered prompt string.
        """
        self._current_rendered_prompt = rendered_prompt

    def record_fault_injection(self, metadata: FaultInjectionMetadata) -> None:
        """Record fault injection metadata in the trajectory.

        Args:
            metadata: Fault injection report from FaultReport.to_trajectory_metadata().
        """
        self.trajectory["metadata"]["fault_injection"] = metadata
        self._write_to_file()

    def record_token_usage(self, usage: TokenUsage) -> None:
        """Record cumulative LLM token usage for this run.

        Called after each agent generate() call with the running total so the
        trajectory always reflects the latest count. The final value after all
        generate() calls is the total tokens consumed by the run.

        Args:
            usage: Dict with prompt_tokens, completion_tokens, total_tokens.
        """
        self.trajectory["metadata"]["token_usage"] = usage
        self._write_to_file()

    def end_phase(self, status: str | None = None) -> None:
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
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                    ).to_dict()
                )

            # Commit the conversation to trajectory
            self._commit_current_conversation()

            # Reset current state
            self._current_phase = None
            self._current_conversation = []
            self._pending_phase_status = None

        # Save to file (outside lock to avoid holding it during I/O)
        self._write_to_file()

    def _commit_current_conversation(self) -> None:
        """Commit the current conversation to the trajectory structure.

        Must be called while holding _conversation_lock.
        """
        if self._current_phase and self._current_conversation:
            phase_key = self._current_phase.value
            if phase_key in self.trajectory:
                # Create a conversation entry with call_id and prompt metadata
                conversation_entry: ConversationEntry = {
                    "call_id": self._current_call_id or 0,
                    "messages": self._current_conversation.copy(),
                }

                # Add prompt version if tracked
                if self._current_prompt_version:
                    conversation_entry["prompt_version"] = self._current_prompt_version

                # Add fallback flag if occurred
                if self._fallback_occurred:
                    conversation_entry["fallback_occurred"] = True

                # Add recorded prompt kwargs if available
                if self._current_prompt_kwargs is not None:
                    conversation_entry["prompt_kwargs"] = self._current_prompt_kwargs

                # Add rendered prompt if recorded
                if self._current_rendered_prompt is not None:
                    conversation_entry["rendered_prompt"] = self._current_rendered_prompt

                # Append the conversation entry
                self.trajectory[phase_key].append(conversation_entry)

            # Update the end_time in the calls list
            if self._current_call_id is not None:
                for call_record in self.trajectory["calls"]:
                    if call_record["call_id"] == self._current_call_id:
                        call_record["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        # Also record prompt metadata in call record
                        if self._current_prompt_version:
                            call_record["prompt_version"] = self._current_prompt_version
                        if self._fallback_occurred:
                            call_record["fallback_occurred"] = True
                        break

            # Reset prompt tracking for next conversation
            self._current_prompt_version = None
            self._current_prompt_kwargs = None
            self._current_rendered_prompt = None
            self._fallback_occurred = False

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
        except (OSError, TypeError, ValueError) as e:
            # Log error but don't crash
            logger.warning(f"Failed to write trajectory file: {e}")

    def _update_latest_link(self) -> None:
        """Update the trajectory.json symlink to point to the latest trajectory."""
        try:
            # Remove existing symlink or file
            if self._latest_link.exists() or self._latest_link.is_symlink():
                self._latest_link.unlink()

            # Create relative symlink
            rel_path = self.trajectory_file.relative_to(self.sds_dir)
            self._latest_link.symlink_to(rel_path)
        except OSError:
            # If symlink fails (e.g., on Windows), just copy the file
            try:
                shutil.copy2(self.trajectory_file, self._latest_link)
            except OSError as e:
                logger.warning(f"Failed to update latest trajectory link: {e}")

    def save(self) -> Path:
        """Save the trajectory to file (alias for _write_to_file).

        Returns:
            Path to the trajectory file.
        """
        self._write_to_file()
        return self.trajectory_file

    def finalize(self, status: str = "completed") -> Path:
        """Finalize the trajectory recording.

        Args:
            status: Final status (completed, failed, interrupted).

        Returns:
            Path to the saved trajectory file.
        """
        if self._finalized:
            return self.trajectory_file

        with self._conversation_lock:
            # Commit any in-progress conversation
            if self._current_phase:
                self._current_conversation.append(
                    TrajectoryMessage(
                        role=MessageRole.ASSISTANT.value,
                        content=f"Run ended with status: {status}",
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                    ).to_dict()
                )
                self._commit_current_conversation()
                self._current_phase = None
                self._current_conversation = []

        # Collect Gemini CLI session files
        sessions = collect_gemini_sessions(
            self.sds_dir,
            self.trajectories_dir,
            self._run_timestamp,
            self.trajectory["metadata"]["start_time"],
        )
        if sessions:
            self.trajectory["gemini_sessions"] = sessions

        self.trajectory["metadata"]["status"] = status
        self._write_to_file()
        self._finalized = True
        return self.trajectory_file

    @contextmanager
    def phase(self, phase: Phase, context: dict[str, Any] | None = None):
        """Context manager for a trajectory phase."""
        self.start_phase(phase, context)
        try:
            yield self
        except BaseException:
            self.end_phase("failed")
            raise
        else:
            status = self._pending_phase_status or "success"
            self.end_phase(status)


# Global recorder instance
_recorder: TrajectoryRecorder | None = None


def init_trajectory(repo_path: Path) -> TrajectoryRecorder:
    """Initialize the global trajectory recorder."""
    global _recorder
    _recorder = TrajectoryRecorder(repo_path)
    return _recorder


def get_trajectory() -> TrajectoryRecorder | None:
    """Get the global trajectory recorder instance."""
    return _recorder


def record_assistant_message(content: str, duration: float | None = None) -> None:
    """Record an assistant response."""
    recorder = get_trajectory()
    if recorder:
        recorder.add_assistant_message(content, duration)


def record_tool_call(
    tool: str,
    args: dict[str, Any],
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    duration: float | None = None,
) -> None:
    """Record a tool call with its output."""
    recorder = get_trajectory()
    if recorder:
        recorder.add_tool_call(tool, args, stdout, stderr, exit_code, duration)


def record_phase_end(status: str | None = None) -> None:
    """End the current phase."""
    recorder = get_trajectory()
    if recorder:
        recorder.end_phase(status)


def finalize_trajectory(status: str = "completed") -> Path | None:
    """Finalize and save the trajectory."""
    recorder = get_trajectory()
    if recorder:
        return recorder.finalize(status)
    return None


def get_current_call_id() -> int | None:
    """Get the current call ID for the active phase.

    Returns:
        Current call_id or None if no trajectory is active.
    """
    recorder = get_trajectory()
    if recorder:
        return recorder.get_current_call_id()
    return None


def get_run_id() -> str | None:
    """Get the unique run ID for the current trajectory.

    Returns:
        Run ID string or None if no trajectory is active.
    """
    recorder = get_trajectory()
    if recorder:
        return recorder.get_run_id()
    return None


def record_phase_start(phase: Phase, context: dict[str, Any] | None = None) -> None:
    """Start a new phase."""
    recorder = get_trajectory()
    if recorder:
        recorder.start_phase(phase, context)


def record_user_message(content: str) -> None:
    """Record a user message."""
    recorder = get_trajectory()
    if recorder:
        recorder.add_user_message(content)


# Wire libs.agent_cli.trajectory context providers to the real thread-local
# implementations so GeminiGenerationSession gets live call/run IDs.
_register_context_providers(get_current_call_id, get_run_id)
