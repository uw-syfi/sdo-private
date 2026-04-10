"""Token usage value object and per-run collector.

Centralizes token-cost accounting for the entire agent stack. ``BaseAgent``
auto-reports into a ``UsageCollector`` after every ``_arun()``; deps types
(``SREDeps``, ``JudgeDeps``, etc.) carry the collector so tool-spawned
subagents share the same accumulator. The orchestrator owns one collector
per problem (typically two: one for the primary phase, one for recovery)
and serializes them at the end of the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    """Token counts for a single agent invocation.

    Field names match the categories pydantic-ai's ``RunUsage`` exposes and
    the keys historically written to ``usage_metrics`` JSON. Frozen so a
    collected entry can't be mutated in place after the fact.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
        }

    @classmethod
    def from_run_usage(cls, run_usage: Any) -> TokenUsage:
        """Build from a pydantic-ai ``RunUsage`` (handles ``None`` fields)."""
        return cls(
            input_tokens=getattr(run_usage, "input_tokens", 0) or 0,
            output_tokens=getattr(run_usage, "output_tokens", 0) or 0,
            cached_input_tokens=getattr(run_usage, "cached_input_tokens", 0) or 0,
        )


@dataclass
class UsageCollector:
    """Per-run usage accumulator.

    Stores raw ``TokenUsage`` entries per ``agent_name`` and computes
    per-agent and grand totals at serialization time. There's no
    double-bookkeeping to keep in sync — ``to_dict()`` is the single
    point of aggregation.

    Asyncio-only — no locking required since the entire crucible run
    lives on one event loop.
    """

    _by_agent: dict[str, list[TokenUsage]] = field(default_factory=dict[str, list[TokenUsage]])

    def add(self, agent_name: str, usage: TokenUsage) -> None:
        self._by_agent.setdefault(agent_name, []).append(usage)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to ``{by_agent: {name: {iterations, total}}, total}``."""
        by_agent: dict[str, Any] = {}
        grand_total = TokenUsage()
        for name, iters in self._by_agent.items():
            agent_total = TokenUsage()
            for u in iters:
                agent_total = agent_total + u
            grand_total = grand_total + agent_total
            by_agent[name] = {
                "iterations": [u.to_dict() for u in iters],
                "total": agent_total.to_dict(),
            }
        return {"by_agent": by_agent, "total": grand_total.to_dict()}
