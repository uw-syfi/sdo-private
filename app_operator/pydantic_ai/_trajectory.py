"""Native Pydantic AI trajectory recording.

Uses Pydantic AI's ``result.all_messages()`` + ``ModelMessagesTypeAdapter``
for serialization. Writes to the same file locations as ``TrajectoryRecorder``.
"""

import json
import time
from pathlib import Path
from typing import Any

from pydantic_ai import RunUsage


class PydanticAITrajectoryRecorder:
    """Records agent trajectories using Pydantic AI's native message format.

    Writes to ``.sds/trajectories/trajectory_{timestamp}.json`` (same as
    TrajectoryRecorder) but uses Pydantic AI's ModelMessage serialization
    instead of custom TrajectoryMessage.
    """

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        sds_dir = repo_path / ".sds"
        trajectories_dir = sds_dir / "trajectories"
        trajectories_dir.mkdir(parents=True, exist_ok=True)

        self._run_timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.trajectory_file = trajectories_dir / f"trajectory_{self._run_timestamp}.json"
        self._latest_link = sds_dir / "trajectory.json"
        self._next_id = 0

        self.trajectory: dict[str, Any] = {
            "metadata": {
                "repo_path": str(repo_path),
                "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "agent_name": None,
                "status": "running",
                "token_usage": None,
                "agent_token_usage": [],
            },
            "phases": [],
        }
        self._write_to_file()

    def set_agent_name(self, name: str) -> None:
        self.trajectory["metadata"]["agent_name"] = name
        self._write_to_file()

    def _next_call_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def record_run(
        self,
        phase: str,
        agent_name: str,
        result: Any,
        context: dict | None = None,
    ) -> None:
        """Record a completed agent run for a phase.

        Args:
            phase: Phase name (e.g. "exploration", "script_generation").
            agent_name: Name of the agent that ran.
            result: The ``RunResult`` from ``agent.run_sync()``.
            context: Optional context dict to store with the phase.
        """
        from pydantic_ai.messages import ModelMessagesTypeAdapter

        messages_json = ModelMessagesTypeAdapter.dump_python(result.all_messages(), mode="json")
        usage = result.usage()

        self.trajectory["phases"].append(
            {
                "phase": phase,
                "agent_name": agent_name,
                "call_id": self._next_call_id(),
                "messages": messages_json,
                "usage": {
                    "input_tokens": usage.input_tokens or 0,
                    "output_tokens": usage.output_tokens or 0,
                    "requests": usage.requests or 0,
                },
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "context": context,
            }
        )
        self._write_to_file()

    def record_token_usage(self, usage: RunUsage) -> None:
        """Record cumulative token usage in metadata."""
        self.trajectory["metadata"]["token_usage"] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "requests": usage.requests,
        }
        self._write_to_file()

    def finalize(self, status: str) -> Path:
        """Set final status, write, and create symlink."""
        self.trajectory["metadata"]["status"] = status
        self.trajectory["metadata"]["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_to_file()

        # Create/update symlink to latest trajectory
        try:
            if self._latest_link.is_symlink() or self._latest_link.exists():
                self._latest_link.unlink()
            self._latest_link.symlink_to(self.trajectory_file)
        except OSError:
            pass

        return self.trajectory_file

    def _write_to_file(self) -> None:
        try:
            self.trajectory_file.parent.mkdir(parents=True, exist_ok=True)
            self.trajectory_file.write_text(json.dumps(self.trajectory, indent=2, default=str))
        except OSError:
            pass
