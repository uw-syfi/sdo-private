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
from typing import Any, cast


@dataclass(frozen=True)
class TokenUsage:
    """Token counts for a single agent invocation.

    Mirrors agentshim's normalized breakdown, and ``to_dict()`` has the same
    keys as ``agentshim.TokenUsage.to_dict()`` (pinned by
    ``tests/unit/test_usage_schema_parity.py``): ``input_tokens`` includes
    cache reads and cache writes, ``uncached_input_tokens`` is the rest, and
    ``output_tokens`` includes ``reasoning_output_tokens``.
    ``cached_input_tokens`` is the deprecated alias of
    ``cache_read_input_tokens``; pass either. Frozen so a collected entry
    can't be mutated in place after the fact.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    turns: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    cache_write_1h_input_tokens: int = 0
    reasoning_output_tokens: int = 0

    def __post_init__(self) -> None:
        read, alias = self.cache_read_input_tokens, self.cached_input_tokens
        if read and alias and read != alias:
            raise ValueError("cached_input_tokens is an alias of cache_read_input_tokens")
        object.__setattr__(self, "cache_read_input_tokens", read or alias)
        object.__setattr__(self, "cached_input_tokens", read or alias)

    @property
    def uncached_input_tokens(self) -> int:
        return max(self.input_tokens - self.cache_read_input_tokens - self.cache_write_input_tokens, 0)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_write_input_tokens=self.cache_write_input_tokens + other.cache_write_input_tokens,
            cache_write_1h_input_tokens=self.cache_write_1h_input_tokens + other.cache_write_1h_input_tokens,
            reasoning_output_tokens=self.reasoning_output_tokens + other.reasoning_output_tokens,
            turns=self.turns + other.turns,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "turns": self.turns,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_write_1h_input_tokens": self.cache_write_1h_input_tokens,
            "uncached_input_tokens": self.uncached_input_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenUsage:
        """Read back a ``to_dict()`` mapping; the derived ``uncached_input_tokens`` is ignored."""
        return cls(
            input_tokens=int(data.get("input_tokens") or 0),
            output_tokens=int(data.get("output_tokens") or 0),
            cached_input_tokens=int(data.get("cached_input_tokens") or 0),
            cache_read_input_tokens=int(data.get("cache_read_input_tokens") or 0),
            cache_write_input_tokens=int(data.get("cache_write_input_tokens") or 0),
            cache_write_1h_input_tokens=int(data.get("cache_write_1h_input_tokens") or 0),
            reasoning_output_tokens=int(data.get("reasoning_output_tokens") or 0),
            turns=int(data.get("turns") or 0),
        )

    @classmethod
    def from_run_usage(cls, run_usage: Any) -> TokenUsage:
        """Build from a pydantic-ai ``RunUsage`` (handles ``None`` fields).

        pydantic-ai's ``input_tokens`` already includes ``cache_read_tokens``
        and ``cache_write_tokens``; reasoning is ``details["reasoning_tokens"]``.
        ``cached_input_tokens`` is read as a fallback for older builds.
        """
        details: object = getattr(run_usage, "details", None)
        reasoning: object = (
            cast("dict[str, object]", details).get("reasoning_tokens") if isinstance(details, dict) else 0
        )
        reasoning_tokens = reasoning if isinstance(reasoning, int) else 0
        read = getattr(run_usage, "cache_read_tokens", 0) or getattr(run_usage, "cached_input_tokens", 0) or 0
        return cls(
            input_tokens=getattr(run_usage, "input_tokens", 0) or 0,
            output_tokens=getattr(run_usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=read,
            cache_write_input_tokens=getattr(run_usage, "cache_write_tokens", 0) or 0,
            reasoning_output_tokens=reasoning_tokens,
            turns=getattr(run_usage, "requests", 0) or 0,
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
