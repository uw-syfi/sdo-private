"""Trajectory data loader for DSPy optimization.

Loads training examples from trajectory files for prompt optimization.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryExample:
    """A single example extracted from a trajectory."""

    trajectory_file: str
    run_id: str
    phase: str
    call_id: int
    prompt: str
    response: str
    success: bool
    iterations: int
    tool_calls: list[dict[str, Any]]
    duration_seconds: float
    token_usage: dict[str, int] | None = None
    prompt_kwargs: dict[str, Any] | None = None
    rendered_prompt: str | None = None
    fallback_occurred: bool = False
    health_check_script: str | None = None  # Content of health_check.sh for quality validation
    fault_injected: bool = False
    fault_ids: list[str] = field(default_factory=list)
    fault_categories: list[str] = field(default_factory=list)
    fault_severities: list[str] = field(default_factory=list)


class TrajectoryDataLoader:
    """Load and parse trajectory files for DSPy training.

    This class reads trajectory JSON files from operator runs and
    extracts examples for prompt optimization.
    """

    def __init__(self, trajectories_dir: Path):
        """Initialize data loader.

        Args:
            trajectories_dir: Directory containing trajectory files
        """
        self.trajectories_dir = Path(trajectories_dir)

    def load_trajectories(self, pattern: str = "trajectory_*.json") -> list[dict[str, Any]]:
        """Load all trajectory JSON files from the directory.

        Args:
            pattern: Glob pattern for trajectory files

        Returns:
            List of parsed trajectory dictionaries
        """
        trajectories = []
        if not self.trajectories_dir.exists():
            return trajectories

        for traj_file in sorted(self.trajectories_dir.glob(pattern)):
            try:
                with open(traj_file) as f:
                    trajectory = json.load(f)
                    trajectory["_file_path"] = str(traj_file)
                    trajectories.append(trajectory)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Failed to load %s: %s", traj_file, e)
                continue

        return trajectories

    def load_examples(
        self,
        phase_filter: str | None = None,
        success_only: bool = False,
    ) -> list[TrajectoryExample]:
        """Load training examples from trajectories.

        Args:
            phase_filter: Only load examples from specific phase (deployment, monitoring, etc.)
            success_only: Only load successful examples

        Returns:
            List of training examples with inputs, outputs, and metrics
        """
        trajectories = self.load_trajectories()
        examples = []

        for trajectory in trajectories:
            file_path = trajectory.get("_file_path", "unknown")
            run_id = trajectory.get("metadata", {}).get("run_id", "unknown")
            overall_success = trajectory.get("metadata", {}).get("status") == "completed"

            # Apply success filter at trajectory level
            if success_only and not overall_success:
                continue

            # Extract fault injection metadata
            fault_meta = trajectory.get("metadata", {}).get("fault_injection", {})
            fault_injected = bool(fault_meta.get("enabled") and fault_meta.get("num_faults_injected", 0) > 0)
            fault_ids = fault_meta.get("fault_ids", [])
            fault_categories = fault_meta.get("categories", [])
            fault_severities = fault_meta.get("severities", [])

            # Extract examples from each phase
            phases_to_process = ["deployment", "monitoring", "script_generation", "exploration"]
            if phase_filter:
                phases_to_process = [phase_filter]

            for phase in phases_to_process:
                phase_data = trajectory.get(phase, [])
                for idx, conversation in enumerate(phase_data):
                    call_id = conversation.get("call_id")
                    messages = conversation.get("messages", [])

                    if not messages:
                        continue

                    # Extract prompt (first user message)
                    prompt = self._extract_prompt(messages)
                    if not prompt:
                        continue

                    # Extract response (assistant messages)
                    response = self._extract_response(messages)

                    # Extract tool calls
                    tool_calls = self._extract_tool_calls(messages)

                    # Calculate metrics
                    success = self._determine_success(messages, phase, overall_success)
                    iterations = idx + 1
                    duration = self._calculate_duration(messages)
                    token_usage = self._extract_token_usage(messages)
                    prompt_kwargs = conversation.get("prompt_kwargs")
                    rendered_prompt = conversation.get("rendered_prompt")
                    fallback_occurred = conversation.get("fallback_occurred", False)

                    # Extract health check script content from repo filesystem
                    health_check_script = self._extract_health_check_script(trajectory)

                    example = TrajectoryExample(
                        trajectory_file=file_path,
                        run_id=run_id,
                        phase=phase,
                        call_id=call_id or 0,
                        prompt=prompt,
                        response=response,
                        success=success,
                        iterations=iterations,
                        tool_calls=tool_calls,
                        duration_seconds=duration,
                        token_usage=token_usage,
                        prompt_kwargs=prompt_kwargs,
                        rendered_prompt=rendered_prompt,
                        fallback_occurred=fallback_occurred,
                        health_check_script=health_check_script,
                        fault_injected=fault_injected,
                        fault_ids=list(fault_ids),
                        fault_categories=list(fault_categories),
                        fault_severities=list(fault_severities),
                    )

                    examples.append(example)

        return examples

    def _extract_prompt(self, messages: list[dict[str, Any]]) -> str:
        """Extract the prompt from messages.

        Prefers the first user message. Falls back to the system message
        for phases (e.g. deployment) that only record system-level context.
        """
        system_content = ""
        for msg in messages:
            if msg.get("role") == "user":
                return msg.get("content", "")
            if msg.get("role") == "system" and not system_content:
                system_content = msg.get("content", "")
        return system_content

    def _extract_response(self, messages: list[dict[str, Any]]) -> str:
        """Extract assistant response (concatenate all assistant messages)."""
        responses = [msg["content"] for msg in messages if msg.get("role") == "assistant" and msg.get("content")]
        return "\n".join(responses)

    def _extract_tool_calls(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Extract all tool calls from messages."""
        tool_calls = [
            {
                "tool": msg.get("tool"),
                "args": msg.get("args"),
                "exit_code": msg.get("exit_code"),
                "stdout": msg.get("stdout", ""),
                "stderr": msg.get("stderr", ""),
            }
            for msg in messages
            if msg.get("role") == "tool_call"
        ]
        return tool_calls

    def _determine_success(self, messages: list[dict[str, Any]], phase: str, overall_success: bool) -> bool:
        """Determine if this conversation was successful."""
        # For deployment phase, check exit codes
        if phase == "deployment":
            tool_calls = [m for m in messages if m.get("role") == "tool_call"]
            if tool_calls:
                last_call = tool_calls[-1]
                exit_code = last_call.get("exit_code")
                if exit_code is not None:
                    if exit_code == 0:
                        return True
                    # exit_code=-1 typically means docker-compose ran in the
                    # background and the shell didn't get a clean exit.  Fall
                    # back to stdout for the real verdict.
                    if exit_code == -1:
                        stdout = (last_call.get("stdout") or "").lower()
                        if "success" in stdout or "healthy" in stdout:
                            return True
                    return False

        # For monitoring, use the exec_summary produced by the agent as the
        # authoritative signal.  It is a concise verdict written by the LLM
        # itself, so checking it avoids false positives from error-related
        # keywords that appear in the detailed body (e.g. "No critical errors
        # detected", "Log Noise/Errors").
        if phase == "monitoring":
            summary = self._extract_exec_summary(messages)
            if summary:
                summary_lower = summary.lower()
                if any(
                    w in summary_lower
                    for w in [
                        "fully operational",
                        "healthy",
                        "all checks passing",
                    ]
                ):
                    return True
                if any(
                    w in summary_lower
                    for w in [
                        "unhealthy",
                        "critical failure",
                        "system down",
                    ]
                ):
                    return False
            # No exec_summary or inconclusive — fall through to overall status
            return overall_success

        # Default to overall trajectory success
        return overall_success

    def _extract_exec_summary(self, messages: list[dict[str, Any]]) -> str | None:
        """Extract the <exec_summary> block from assistant messages, if present."""
        for msg in messages:
            if msg.get("role") == "assistant":
                match = re.search(
                    r"<exec_summary>(.*?)</exec_summary>",
                    msg.get("content", ""),
                    re.DOTALL,
                )
                if match:
                    return match.group(1).strip()
        return None

    def _calculate_duration(self, messages: list[dict[str, Any]]) -> float:
        """Calculate total duration from message timestamps and durations."""
        total_duration = 0.0
        for msg in messages:
            duration = msg.get("duration_seconds")
            if duration:
                total_duration += duration
        return total_duration

    # Gemini CLI session files store per-message token counts, but those
    # counts are not copied into the trajectory messages.  Estimate from
    # character lengths instead (~4 chars / token is the standard
    # approximation for most LLM tokenizers).
    _CHARS_PER_TOKEN = 4.0

    def _extract_token_usage(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Estimate token usage from message content lengths.

        Input tokens are approximated from system, user, and tool_call
        messages (including args and stdout).  Output tokens come from
        assistant messages.  Returns None when there is no content to
        measure.
        """
        input_chars = 0
        output_chars = 0

        for msg in messages:
            content = msg.get("content") or ""
            role = msg.get("role")

            if role in ("system", "user", "tool_call"):
                input_chars += len(content)
                # tool args and stdout are part of the context fed back to the model
                if msg.get("args"):
                    input_chars += len(str(msg["args"]))
                if msg.get("stdout"):
                    input_chars += len(msg["stdout"])
            elif role == "assistant":
                output_chars += len(content)

        if input_chars == 0 and output_chars == 0:
            return None

        return {
            "input": round(input_chars / self._CHARS_PER_TOKEN),
            "output": round(output_chars / self._CHARS_PER_TOKEN),
            "estimated": True,
        }

    def _extract_health_check_script(self, trajectory: dict[str, Any]) -> str | None:
        """Extract health_check.sh content from the repository filesystem.

        Args:
            trajectory: Trajectory dictionary with metadata.repo_path

        Returns:
            Content of health_check.sh file, or None if not found
        """
        repo_path = trajectory.get("metadata", {}).get("repo_path")
        if not repo_path:
            return None
        repo = Path(repo_path).resolve()
        if not repo.is_absolute():
            return None
        health_check_path = (repo / ".sds" / "health_check.sh").resolve()
        if not health_check_path.is_relative_to(repo):
            return None
        try:
            if health_check_path.exists():
                return health_check_path.read_text()
        except OSError as e:
            logger.debug(f"Could not read health check: {e}")

        return None
