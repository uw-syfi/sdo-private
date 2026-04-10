"""Unit tests for TokenUsage / UsageCollector and BaseAgent auto-reporting."""

from __future__ import annotations

from typing import Any

import pytest

from libs.pydantic_agent import BaseAgent, TokenUsage, UsageCollector

# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------


class TestTokenUsage:
    def test_default_is_all_zero(self):
        u = TokenUsage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cached_input_tokens == 0

    def test_to_dict_round_trip(self):
        u = TokenUsage(input_tokens=10, output_tokens=5, cached_input_tokens=2)
        assert u.to_dict() == {
            "input_tokens": 10,
            "output_tokens": 5,
            "cached_input_tokens": 2,
        }

    def test_add_sums_each_field(self):
        a = TokenUsage(input_tokens=10, output_tokens=5, cached_input_tokens=1)
        b = TokenUsage(input_tokens=3, output_tokens=7, cached_input_tokens=2)
        c = a + b
        assert c == TokenUsage(input_tokens=13, output_tokens=12, cached_input_tokens=3)

    def test_frozen_prevents_mutation(self):
        u = TokenUsage(input_tokens=10)
        with pytest.raises((AttributeError, Exception)):
            u.input_tokens = 99  # type: ignore[misc]

    def test_from_run_usage_handles_none_fields(self):
        class Stub:
            input_tokens = None
            output_tokens = 5

        u = TokenUsage.from_run_usage(Stub())
        assert u.input_tokens == 0
        assert u.output_tokens == 5
        assert u.cached_input_tokens == 0

    def test_from_run_usage_missing_cached_field(self):
        """Older RunUsage builds may lack cached_input_tokens entirely."""

        class Stub:
            input_tokens = 100
            output_tokens = 50

        u = TokenUsage.from_run_usage(Stub())
        assert u.cached_input_tokens == 0


# ---------------------------------------------------------------------------
# UsageCollector
# ---------------------------------------------------------------------------


class TestUsageCollector:
    def test_empty_to_dict(self):
        c = UsageCollector()
        d = c.to_dict()
        assert d == {
            "by_agent": {},
            "total": {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0},
        }

    def test_add_one_entry(self):
        c = UsageCollector()
        c.add("sre-diagnosis", TokenUsage(input_tokens=10, output_tokens=5))
        d = c.to_dict()
        assert d["by_agent"]["sre-diagnosis"]["total"] == {
            "input_tokens": 10,
            "output_tokens": 5,
            "cached_input_tokens": 0,
        }
        assert len(d["by_agent"]["sre-diagnosis"]["iterations"]) == 1
        assert d["total"]["input_tokens"] == 10

    def test_multiple_iterations_under_same_name_accumulate_into_total(self):
        c = UsageCollector()
        c.add("sre-diagnosis", TokenUsage(input_tokens=10))
        c.add("sre-diagnosis", TokenUsage(input_tokens=7))
        c.add("sre-diagnosis", TokenUsage(input_tokens=3))
        d = c.to_dict()
        assert len(d["by_agent"]["sre-diagnosis"]["iterations"]) == 3
        assert d["by_agent"]["sre-diagnosis"]["total"]["input_tokens"] == 20

    def test_grand_total_sums_across_distinct_agents(self):
        c = UsageCollector()
        c.add("a", TokenUsage(input_tokens=10, output_tokens=5))
        c.add("b", TokenUsage(input_tokens=20, output_tokens=8, cached_input_tokens=1))
        c.add("c", TokenUsage(input_tokens=3))
        d = c.to_dict()
        assert d["total"] == {
            "input_tokens": 33,
            "output_tokens": 13,
            "cached_input_tokens": 1,
        }

    def test_total_matches_sum_of_per_agent_totals(self):
        c = UsageCollector()
        c.add("a", TokenUsage(input_tokens=10))
        c.add("a", TokenUsage(input_tokens=5))
        c.add("b", TokenUsage(input_tokens=3))
        d = c.to_dict()
        per_agent_sum = sum(agent["total"]["input_tokens"] for agent in d["by_agent"].values())
        assert d["total"]["input_tokens"] == per_agent_sum


# ---------------------------------------------------------------------------
# BaseAgent auto-reporting
# ---------------------------------------------------------------------------


class _StubRunUsage:
    """Mimics pydantic_ai.usage.RunUsage shape for the auto-reporter."""

    def __init__(self, input_tokens: int = 0, output_tokens: int = 0, cached_input_tokens: int = 0) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cached_input_tokens = cached_input_tokens


class _StubBaseAgent(BaseAgent[None]):
    """Minimal subclass that bypasses pydantic-ai entirely.

    Overrides ``_arun`` to skip the agent.iter loop and just stamp a fake
    ``current_run_usage`` so we can validate the auto-report path in
    isolation.
    """

    def __init__(self, fake_usage: _StubRunUsage, *, usage_collector: UsageCollector | None) -> None:
        super().__init__(None, agent_name="stub-agent", usage_collector=usage_collector)
        self._fake_usage = fake_usage

    async def _arun(self, prompt: str, *, _run_ctx: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        # Simulate the streaming loop populating current_run_usage, then run
        # the same auto-report path as real BaseAgent._arun.
        self.current_run_usage = self._fake_usage  # type: ignore[assignment]
        self._report_usage()
        return None


@pytest.mark.asyncio
class TestBaseAgentAutoReport:
    async def test_collector_records_after_arun(self):
        collector = UsageCollector()
        agent = _StubBaseAgent(_StubRunUsage(input_tokens=42, output_tokens=7), usage_collector=collector)
        await agent._arun("ignored")

        d = collector.to_dict()
        assert "stub-agent" in d["by_agent"]
        assert d["by_agent"]["stub-agent"]["total"]["input_tokens"] == 42
        assert d["by_agent"]["stub-agent"]["total"]["output_tokens"] == 7

    async def test_no_collector_is_a_noop(self):
        agent = _StubBaseAgent(_StubRunUsage(input_tokens=99), usage_collector=None)
        # Should not raise.
        await agent._arun("ignored")

    async def test_multiple_arun_calls_accumulate_into_iterations(self):
        collector = UsageCollector()
        agent = _StubBaseAgent(_StubRunUsage(input_tokens=10), usage_collector=collector)
        await agent._arun("first")
        agent._fake_usage = _StubRunUsage(input_tokens=20)
        await agent._arun("second")

        d = collector.to_dict()
        bucket = d["by_agent"]["stub-agent"]
        assert len(bucket["iterations"]) == 2
        assert bucket["total"]["input_tokens"] == 30
