"""Trajectory data loader for DSPy optimization.

Loads training examples from trajectory files for prompt optimization.
"""

import json
from typing import List, Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass


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
    tool_calls: List[Dict[str, Any]]
    duration_seconds: float
    token_usage: Optional[Dict[str, int]] = None


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

    def load_trajectories(self, pattern: str = "trajectory_*.json") -> List[Dict[str, Any]]:
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
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Failed to load {traj_file}: {e}")
                continue

        return trajectories

    def load_examples(
        self,
        phase_filter: Optional[str] = None,
        success_only: bool = False,
    ) -> List[TrajectoryExample]:
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

            # Extract examples from each phase
            phases_to_process = ["deployment", "monitoring", "script_generation"]
            if phase_filter:
                phases_to_process = [phase_filter]

            for phase in phases_to_process:
                phase_data = trajectory.get(phase, [])
                for conversation in phase_data:
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
                    iterations = len([m for m in messages if m.get("role") == "assistant"])
                    duration = self._calculate_duration(messages)
                    token_usage = self._extract_token_usage(messages)

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
                    )

                    examples.append(example)

        return examples

    def _extract_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """Extract the user prompt from messages."""
        for msg in messages:
            if msg.get("role") == "user":
                return msg.get("content", "")
        return ""

    def _extract_response(self, messages: List[Dict[str, Any]]) -> str:
        """Extract assistant response (concatenate all assistant messages)."""
        responses = []
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("content"):
                responses.append(msg["content"])
        return "\n".join(responses)

    def _extract_tool_calls(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract all tool calls from messages."""
        tool_calls = []
        for msg in messages:
            if msg.get("role") == "tool_call":
                tool_calls.append({
                    "tool": msg.get("tool"),
                    "args": msg.get("args"),
                    "exit_code": msg.get("exit_code"),
                    "stdout": msg.get("stdout", ""),
                    "stderr": msg.get("stderr", ""),
                })
        return tool_calls

    def _determine_success(
        self,
        messages: List[Dict[str, Any]],
        phase: str,
        overall_success: bool
    ) -> bool:
        """Determine if this conversation was successful."""
        # For deployment phase, check exit codes
        if phase == "deployment":
            tool_calls = [m for m in messages if m.get("role") == "tool_call"]
            if tool_calls:
                # Check last tool call exit code
                last_call = tool_calls[-1]
                exit_code = last_call.get("exit_code")
                if exit_code is not None:
                    return exit_code == 0

        # For monitoring, check if no errors were reported
        if phase == "monitoring":
            for msg in messages:
                if msg.get("role") == "assistant":
                    content = msg.get("content", "").lower()
                    if any(word in content for word in ["error", "failed", "failure"]):
                        return False

        # Default to overall trajectory success
        return overall_success

    def _calculate_duration(self, messages: List[Dict[str, Any]]) -> float:
        """Calculate total duration from message timestamps and durations."""
        total_duration = 0.0
        for msg in messages:
            duration = msg.get("duration_seconds")
            if duration:
                total_duration += duration
        return total_duration

    def _extract_token_usage(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        """Extract token usage from messages (if available)."""
        # Token usage would be added in Phase 2 trajectory enhancement
        # For now, return None
        return None
