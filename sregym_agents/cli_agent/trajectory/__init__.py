"""Trajectory collection + retrieval for cli_agent.

A *trajectory* is the raw turn-by-turn record of one agent run (thinking,
tool calls, tool results, usage). Unlike ``cli_agent.memory`` — which stores an
LLM-distilled ``Lesson`` per run — this package persists the unedited run so it
can be retrieved later, either by an LLM searching over compact digests or by
embedding-based (RAG) similarity.

Storage is JSONL, one file per run, keyed ``{app}/{problem_id}__{ts}.jsonl``.
The store must live OUTSIDE the ephemeral ``SREGYM_EXP_ENV`` workdir so it
accumulates across runs (see ``docs/cli-agent-trajectory.md``).
"""

from __future__ import annotations

from .store import (
    TrajectoryDigest,
    TrajectoryEvent,
    TrajectoryFindings,
    TrajectoryMeta,
    TrajectoryRecorder,
    TrajectoryStore,
)

__all__ = [
    "TrajectoryDigest",
    "TrajectoryEvent",
    "TrajectoryFindings",
    "TrajectoryMeta",
    "TrajectoryRecorder",
    "TrajectoryStore",
]
