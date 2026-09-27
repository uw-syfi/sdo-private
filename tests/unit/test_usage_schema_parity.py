"""The crucible usage record and agentshim's share one ``to_dict()`` shape.

``libs/pydantic_agent/_usage.py`` (legacy benchmark agents) and agentshim's
``TokenUsage`` feed the same analysis, so their serialized keys and field
semantics must agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import agentshim

from libs.pydantic_agent._usage import TokenUsage


def test_to_dict_keys_match_agentshim() -> None:
    assert set(TokenUsage().to_dict()) == set(agentshim.TokenUsage().to_dict())


def test_the_same_counts_serialize_identically() -> None:
    counts: dict[str, int] = {
        "input_tokens": 1_000,
        "cache_read_input_tokens": 600,
        "cache_write_input_tokens": 300,
        "cache_write_1h_input_tokens": 100,
        "output_tokens": 80,
        "reasoning_output_tokens": 20,
        "turns": 3,
    }
    assert TokenUsage(**counts).to_dict() == agentshim.TokenUsage(**counts).to_dict()


@dataclass
class _RunUsage:
    """The pydantic-ai ``RunUsage`` fields ``from_run_usage`` reads."""

    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    details: dict[str, Any] = field(default_factory=dict[str, Any])


def test_pydantic_ai_cache_and_reasoning_counts_are_read() -> None:
    usage = TokenUsage.from_run_usage(
        _RunUsage(
            input_tokens=1_000,
            cache_read_tokens=600,
            cache_write_tokens=300,
            output_tokens=80,
            requests=3,
            details={"reasoning_tokens": 20},
        )
    )
    assert usage.cache_read_input_tokens == usage.cached_input_tokens == 600
    assert usage.cache_write_input_tokens == 300
    assert usage.uncached_input_tokens == 100
    assert usage.reasoning_output_tokens == 20
