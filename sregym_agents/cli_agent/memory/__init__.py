"""Persistent incident memory for cli_agent.

A per-app JSONL store of verified **lessons** (see
``docs/cli-agent-memory.md``). The agent reads via the ``recall`` MCP tool
(:mod:`.recall_server`); the driver writes out-of-band after a problem is
confirmed solved, deduping inline via an LLM upsert (:mod:`.extract`).
"""

from __future__ import annotations

from .store import Lesson, LessonStore, slugify

__all__ = ["Lesson", "LessonStore", "slugify"]
