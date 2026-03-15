"""Unit tests for TrajectoryMiddleware."""

from __future__ import annotations

import json

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from libs.agent_mw import TrajectoryMiddleware
from libs.pydantic_agent import BaseAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def echo(ctx, message: str) -> str:
    return f"echo: {message}"


def _make_agent(middleware=None, call_tools="all"):
    class ConcreteAgent(BaseAgent):
        def __init__(self):
            super().__init__(None, agent_name="traj-agent", middleware=middleware or [])
            self._agent = Agent(
                TestModel(call_tools=call_tools),
                deps_type=type(None),
                output_type=str,
                tools=[echo],
            )

    return ConcreteAgent()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_after_run_appends_json_line(tmp_path):
    out = tmp_path / "traj.jsonl"
    mw = TrajectoryMiddleware(out)
    agent = _make_agent(middleware=[mw], call_tools=[])
    agent._run("hello")

    assert out.exists()
    lines = out.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["agent_name"] == "traj-agent"
    assert "timestamp" in record
    assert "messages" in record
    assert "usage" in record
    assert "input_tokens" in record["usage"]
    assert "output_tokens" in record["usage"]


def test_after_run_appends_multiple_lines(tmp_path):
    out = tmp_path / "traj.jsonl"
    mw = TrajectoryMiddleware(out)
    agent = _make_agent(middleware=[mw], call_tools=[])
    agent._run("first")
    agent._run("second")

    lines = out.read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        record = json.loads(line)
        assert record["agent_name"] == "traj-agent"


def test_run_ctx_included_in_record(tmp_path):
    out = tmp_path / "traj.jsonl"
    mw = TrajectoryMiddleware(out)
    agent = _make_agent(middleware=[mw], call_tools=[])
    ctx = {"stage": "diagnosis", "iteration": 1, "role": "sre"}
    agent._run("prompt", _run_ctx=ctx)

    record = json.loads(out.read_text().strip())
    assert record["run_ctx"] == ctx


def test_creates_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "deep" / "traj.jsonl"
    mw = TrajectoryMiddleware(out)
    agent = _make_agent(middleware=[mw], call_tools=[])
    agent._run("hi")
    assert out.exists()


def test_record_is_valid_json_per_line(tmp_path):
    out = tmp_path / "traj.jsonl"
    mw = TrajectoryMiddleware(out)
    agent = _make_agent(middleware=[mw])
    agent._run("hi")

    for line in out.read_text().splitlines():
        # Should not raise
        json.loads(line)
