"""Unit tests for spawn_subagent tool in build_tools()."""

from unittest.mock import MagicMock, patch

import pytest

from app_operator.langgraph.tools import MAX_SUBAGENT_DEPTH, build_tools


@pytest.fixture
def repo_path(tmp_path):
    return tmp_path


def tool_names(tools):
    return [t.name for t in tools]


class TestBuildToolsSpawnSubagent:
    def test_no_llm_excludes_spawn_subagent(self, repo_path):
        tools = build_tools(repo_path)
        assert "spawn_subagent" not in tool_names(tools)

    def test_with_llm_includes_spawn_subagent(self, repo_path):
        mock_llm = MagicMock()
        tools = build_tools(repo_path, llm=mock_llm)
        assert "spawn_subagent" in tool_names(tools)

    def test_spawn_subagent_returns_response_text(self, repo_path):
        mock_llm = MagicMock()
        sink: list = []

        # Build a fake AI message with text content and usage_metadata
        from langchain_core.messages import AIMessage

        ai_msg = AIMessage(content="subtask done")
        ai_msg.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

        # create_react_agent().stream() yields chunks in "updates" stream mode
        fake_chunk = {"agent": {"messages": [ai_msg]}}
        mock_agent = MagicMock()
        mock_agent.stream.return_value = iter([fake_chunk])

        # Keep patch active through spawn.invoke so the closure captures the mock
        with patch("app_operator.langgraph.tools.create_react_agent", return_value=mock_agent):
            tools = build_tools(repo_path, llm=mock_llm, token_sink=sink)
            spawn = next(t for t in tools if t.name == "spawn_subagent")
            result = spawn.invoke({"prompt": "do something"})

        assert result == "subtask done"
        assert len(sink) == 1
        assert sink[0]["agent"] == "subagent_d0"
        assert sink[0]["input"] == 10
        assert sink[0]["output"] == 5
        assert sink[0]["total"] == 15

    def test_spawn_subagent_at_max_depth_returns_error(self, repo_path):
        from app_operator.langgraph.tools import _build_spawn_subagent

        sink: list = []
        tool_fn = _build_spawn_subagent(
            llm=MagicMock(),
            base_tools=[],
            compaction_hook=None,
            current_depth=MAX_SUBAGENT_DEPTH,
            max_depth=MAX_SUBAGENT_DEPTH,
            token_sink=sink,
        )
        result = tool_fn.invoke({"prompt": "nested"})
        assert "Maximum subagent depth" in result
        assert len(sink) == 0

    def test_spawn_subagent_no_ai_messages_returns_fallback(self, repo_path):
        mock_llm = MagicMock()
        sink: list = []

        # Stream with no AI messages
        mock_agent = MagicMock()
        mock_agent.stream.return_value = iter([{"agent": {"messages": []}}])

        with patch("app_operator.langgraph.tools.create_react_agent", return_value=mock_agent):
            tools = build_tools(repo_path, llm=mock_llm, token_sink=sink)
            spawn = next(t for t in tools if t.name == "spawn_subagent")
            result = spawn.invoke({"prompt": "do something"})

        assert result == "[No response from subagent]"
        assert len(sink) == 0
