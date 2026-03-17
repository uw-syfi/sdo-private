"""Native Pydantic AI trajectory recording.

Uses Pydantic AI's ``result.all_messages()`` + ``ModelMessagesTypeAdapter``
for serialization. Writes one JSONL file per agent run into a session directory,
with a ``metadata.json`` index file.
"""

import json
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic_ai import RunUsage


def _sanitize_for_filename(name: str) -> str:
    """Lowercase, replace non-alphanumeric runs with underscores."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class PydanticAITrajectoryRecorder:
    """Records agent trajectories using Pydantic AI's native message format.

    Creates a session directory ``.sds/trajectories/{timestamp}/`` and writes:
    - One JSONL file per agent run (e.g. ``001_deployment_health_agent.jsonl``)
    - A ``metadata.json`` index mapping phases to lists of trajectory filenames
    """

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        sds_dir = repo_path / ".sds"
        trajectories_dir = sds_dir / "trajectories"

        self._run_timestamp = time.strftime("%Y%m%d-%H%M%S")
        self._session_dir = trajectories_dir / self._run_timestamp
        self._session_dir.mkdir(parents=True, exist_ok=True)

        self.trajectory_file = self._session_dir / "metadata.json"
        self._latest_link = sds_dir / "trajectory.json"
        self._counter = 0
        self._total_usage: RunUsage = RunUsage()

        self._metadata: dict[str, Any] = {
            "metadata": {
                "repo_path": str(repo_path),
                "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "running",
                "token_usage": None,
            },
            "phases": {},
        }
        self._write_metadata()

    def next_agent_path(self, phase: str, agent_name: str) -> Path:
        """Generate the next JSONL file path for a run and register it in metadata.

        Args:
            phase: Phase name (e.g. "deployment", "monitoring").
            agent_name: Name of the agent that will run.

        Returns:
            Absolute path to the new JSONL file.
        """
        self._counter += 1
        sanitized_phase = _sanitize_for_filename(phase)
        sanitized_agent = _sanitize_for_filename(agent_name)
        filename = f"{self._counter:03d}_{sanitized_phase}_{sanitized_agent}.jsonl"
        path = self._session_dir / filename

        phases = self._metadata["phases"]
        if phase not in phases:
            phases[phase] = []
        phases[phase].append(filename)
        self._write_metadata()

        return path

    def record_usage(self, usage: RunUsage) -> None:
        """Accumulate token usage in memory (written to disk only at finalize)."""
        self._total_usage += usage

    @property
    def total_usage(self) -> RunUsage:
        """Cumulative token usage across all recorded runs."""
        return self._total_usage

    def finalize(self, status: str) -> Path:
        """Set final status, write metadata, and symlink ``.sds/trajectory.json``."""
        self._metadata["metadata"]["status"] = status
        self._metadata["metadata"]["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._metadata["metadata"]["token_usage"] = {
            "input_tokens": self._total_usage.input_tokens,
            "output_tokens": self._total_usage.output_tokens,
            "requests": self._total_usage.requests,
        }
        self._write_metadata()

        try:
            if self._latest_link.is_symlink() or self._latest_link.exists():
                self._latest_link.unlink()
            self._latest_link.symlink_to(self.trajectory_file)
        except OSError:
            pass

        return self.trajectory_file

    def _write_metadata(self) -> None:
        try:
            self.trajectory_file.write_text(json.dumps(self._metadata, indent=2, default=str))
        except OSError as e:
            logger.warning(
                "Failed to write trajectory metadata to {path}: {error}",
                path=self.trajectory_file,
                error=e,
            )


class RecorderPathProvider:
    """Generates a new JSONL file per run, registered in the recorder's metadata."""

    def __init__(self, recorder: PydanticAITrajectoryRecorder) -> None:
        self._recorder = recorder

    def get_path(self, agent_name: str, run_ctx: dict[str, Any] | None) -> Path:
        phase = str(run_ctx["phase"]) if run_ctx else "unknown"
        an = run_ctx.get("agent_name", agent_name) if run_ctx else agent_name
        return self._recorder.next_agent_path(phase, an)
