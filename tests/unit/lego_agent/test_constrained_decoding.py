import pytest
import asyncio
from unittest.mock import MagicMock, patch
from lego_agent.engine import LegoAgentEngine
from app_operator.config import Config, AgentConfig, OperatorConfig


@pytest.fixture
def mock_config():
    return Config(
        agent=AgentConfig(provider="gemini", model="gemini-1.5-pro"),
        operator=OperatorConfig(),
    )


def test_submit_response_tool_usage(tmp_path, mock_config):
    async def _test_body():
        # Mock dependencies
        mock_loader = MagicMock()
        mock_loader.render.return_value = "system prompt"
        mock_io = MagicMock()

        # Mock agent
        mock_agent = MagicMock()

        # Define events to simulate tool usage
        async def mock_astream_events(*args, **kwargs):
            # Event 1: Thinking
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="I am ready.")},
            }
            # Event 2: Tool Use
            yield {
                "event": "on_tool_start",
                "name": "submit_response",
                "data": {
                    "input": {
                        "status": "ready",
                        "yaml_config": "workflow: ...",
                        "python_script": 'import lego_agent.runtime\nMAX_ITERATIONS = 10\nif __name__ == "__main__": pass',
                    }
                },
            }
            # Event 3: Tool End
            yield {
                "event": "on_tool_end",
                "name": "submit_response",
                "data": {"output": "Response submitted."},
            }

        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [MagicMock(content="fallback")]}

        mock_agent.ainvoke = mock_ainvoke
        mock_agent.astream_events = mock_astream_events
        # Patch create_react_agent and build_llm
        with patch("lego_agent.engine.create_react_agent", return_value=mock_agent):
            with patch("lego_agent.engine.build_llm"):
                engine = LegoAgentEngine(
                    config=mock_config,
                    prompt_loader=mock_loader,
                    io=mock_io,
                    loop_bound=10,
                    max_clarifications=1,
                    agent_timeout=10,
                    output_dir=tmp_path,
                    work_dir=tmp_path,
                )

                result = await engine.run_async("test prompt")

                assert result.script_text is not None
                assert "MAX_ITERATIONS = 10" in result.script_text

    asyncio.run(_test_body())
