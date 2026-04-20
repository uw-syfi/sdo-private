"""Tests for load_cli_agent_turns / load_cli_agent_tokens analyzer helpers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bench.sregym_analysis.summarize_results import (
    load_cli_agent_tokens,
    load_cli_agent_turns,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write(
    tmp_path: Path,
    problem_id: str,
    ts: str,
    total: dict[str, object] | None,
) -> None:
    (tmp_path / f"cli_agent_results_{problem_id}_{ts}.json").write_text(
        json.dumps(
            {
                "problem_id": problem_id,
                "usage_metrics": {"total": total} if total is not None else None,
            }
        )
    )


def test_turns_and_tokens_round_trip(tmp_path: Path):
    _write(
        tmp_path,
        "probA",
        "20260420_180353",
        {
            "input_tokens": 100,
            "output_tokens": 40,
            "cached_input_tokens": 30,
            "turns": 5,
            "total_cost_usd": 0.5,
            "provider": "claude",
        },
    )
    _write(
        tmp_path,
        "probB",
        "20260420_180353",
        {
            "input_tokens": 200,
            "output_tokens": 80,
            "cached_input_tokens": 0,
            "turns": 11,
            "total_cost_usd": None,
            "provider": "codex",
        },
    )

    turns = load_cli_agent_turns(str(tmp_path))
    tokens = load_cli_agent_tokens(str(tmp_path))

    assert turns == {"probA": 5, "probB": 11}
    # tokens = input + output (cached is subset of input)
    assert tokens == {"probA": 140, "probB": 280}


def test_gemini_zero_tokens_skipped(tmp_path: Path):
    _write(
        tmp_path,
        "probC",
        "20260420_180353",
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "turns": 7,
            "total_cost_usd": None,
            "provider": "gemini",
        },
    )

    # Turns still surface for gemini (that's its primary signal).
    assert load_cli_agent_turns(str(tmp_path)) == {"probC": 7}
    # Tokens are filtered (0 would poison cross-provider comparisons).
    assert load_cli_agent_tokens(str(tmp_path)) == {}


def test_missing_usage_metrics_ignored(tmp_path: Path):
    _write(tmp_path, "probD", "20260420_180353", None)
    assert load_cli_agent_turns(str(tmp_path)) == {}
    assert load_cli_agent_tokens(str(tmp_path)) == {}


def test_empty_dir_returns_empty(tmp_path: Path):
    assert load_cli_agent_turns(str(tmp_path)) == {}
    assert load_cli_agent_tokens(str(tmp_path)) == {}
