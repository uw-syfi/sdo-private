from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app_operator.cli_agent.agents.base import OperatorAgent


class _TestAgent(OperatorAgent[str]):
    def prepare(self, **kwargs: Any) -> str:
        return "prompt"

    def parse(self, response: str) -> str:
        return f"parsed: {response}"


@pytest.fixture
def mock_agent_context() -> MagicMock:
    ctx = MagicMock()
    ctx.coding_agent.generate.return_value = "response"
    ctx.repo_path = "/tmp"
    ctx.operator_config.agent_timeout = 120
    return ctx


def test_operator_agent_run(mock_agent_context: MagicMock):
    agent = _TestAgent(ctx=mock_agent_context)
    result = agent.run()
    assert result == "parsed: response"
    mock_agent_context.coding_agent.generate.assert_called_once_with(
        "prompt",
        cwd="/tmp",
        timeout=120,
    )


def test_operator_agent_execute(mock_agent_context: MagicMock):
    agent = _TestAgent(ctx=mock_agent_context)
    response = agent.execute("test_prompt", timeout=60)
    assert response == "response"
    mock_agent_context.coding_agent.generate.assert_called_once_with(
        "test_prompt",
        cwd="/tmp",
        timeout=60,
    )
