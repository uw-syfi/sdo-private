import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app_operator.config import AgentConfig, Config, OperatorConfig
from lego_agent.engine import LegoAgentEngine
from lego_agent.io import UserIO
from lego_agent.prompts import get_loader


class MockIO(UserIO):
    def __init__(self, answers=None):
        self.answers = answers or []
        self.questions_asked = []
        self.info_messages = []
        self.stream_output = ""

    def read_prompt(self) -> str:
        return "test prompt"

    async def ask_questions(self, questions):
        self.questions_asked.extend(questions)
        return self.answers.pop(0) if self.answers else ["mock answer"] * len(questions)

    async def prompt_int(self, label: str) -> int:
        return 10

    def info(self, message: str) -> None:
        self.info_messages.append(message)

    def print_stream(self, text: str) -> None:
        self.stream_output += text

    def render_thinking_chunk(self, text: str) -> None:
        self.stream_output += text

    def render_tool_start(self, name: str, inputs: str) -> None:
        self.info_messages.append(f"[Tool Use] {name}")

    def render_tool_end(self, name: str, output: str, status: str) -> None:
        self.info_messages.append(f"[Tool Result] {name}: {status}")

    def render_error(self, message: str) -> None:
        self.info_messages.append(f"Error: {message}")

    def render_success(self, message: str) -> None:
        self.info_messages.append(f"Success: {message}")

    def render_info(self, message: str) -> None:
        self.info_messages.append(f"Info: {message}")

    def render_graph(self, config: dict) -> None:
        self.info_messages.append(f"[Graph Render] {config}")


@pytest.fixture
def mock_config():
    return Config(
        agent=AgentConfig(provider="gemini", model="gemini-1.5-pro"),
        operator=OperatorConfig(),
    )


@pytest.fixture
def mock_io():
    return MockIO()


@pytest.fixture
def engine(tmp_path, mock_config, mock_io):
    loader = get_loader()
    return LegoAgentEngine(
        config=mock_config,
        prompt_loader=loader,
        io=mock_io,
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=tmp_path,
        work_dir=tmp_path,
    )


def test_engine_happy_path(engine, tmp_path):
    with (
        patch("lego_agent.engine.create_react_agent") as mock_create_agent,
        patch("lego_agent.engine.build_llm"),
    ):
        mock_agent = MagicMock()

        async def mock_astream_events(*args, **kwargs):
            yield {
                "event": "on_chat_model_stream",
                "data": {
                    "chunk": MagicMock(
                        content=r"""
```json
{
    "status": "ready",
    "yaml_config": "workflow:\n  name: test"
}
```
"""
                    )
                },
            }

        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [MagicMock(content='{"status": "error", "output": "fallback"}')]}

        mock_agent.astream_events = mock_astream_events
        mock_agent.ainvoke = mock_ainvoke
        mock_create_agent.return_value = mock_agent

        result = asyncio.run(engine.run_async("do something"))

        assert result.script_text is not None
        assert (tmp_path / result.script_path.parent.name / "generated_script.py").exists()
        assert result.clarifications == []


def test_engine_clarification_loop(engine, mock_io):
    # Sequence: Clarify -> Ready
    # Since create_react_agent is called in a loop, we need to provide different mocks or behavior for each call
    # Or better, the mock_astream_events can yield different things based on
    # the prompt or just sequential calls.

    with (
        patch("lego_agent.engine.create_react_agent") as mock_create_agent,
        patch("lego_agent.engine.build_llm"),
    ):
        mock_agent = MagicMock()

        # We need an iterator for the responses
        responses = [
            r'{"status": "clarify", "questions": ["Q1"]}',
            r"""
            {
                "status": "ready",
                "yaml_config": "workflow:\n  name: test"
            }
            """,
        ]

        call_count = 0

        async def mock_astream_events(*args, **kwargs):
            nonlocal call_count
            response = responses[call_count]
            call_count += 1
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content=response)},
            }

        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [MagicMock(content='{"status": "error", "output": "fallback"}')]}

        mock_agent.astream_events = mock_astream_events
        mock_agent.ainvoke = mock_ainvoke
        mock_create_agent.return_value = mock_agent

        # Prepare IO with answer
        mock_io.answers = [["A1"]]

        result = asyncio.run(engine.run_async("task"))

        assert len(mock_io.questions_asked) == 1
        assert mock_io.questions_asked[0] == "Q1"
        assert result.clarifications == [("Q1", "A1")]
        assert "MAX_ITERATIONS = 5" in result.script_text


def test_engine_validation_failure_and_repair(engine):
    with (
        patch("lego_agent.engine.create_react_agent") as mock_create_agent,
        patch("lego_agent.engine.build_llm"),
    ):
        mock_agent = MagicMock()

        # First call: Not JSON
        # Second call (repair via ainvoke): Valid JSON

        first_call_done = False

        async def mock_astream_events(*args, **kwargs):
            nonlocal first_call_done
            if not first_call_done:
                first_call_done = True
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": MagicMock(content="Not JSON")},
                }
            else:
                # Should not be reached via astream_events in this test logic because
                # repair uses ainvoke
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": MagicMock(content="Should not be here")},
                }

        async def mock_ainvoke_impl(*args, **kwargs):
            # This is the repair call
            return {
                "messages": [MagicMock(content=r"""{"status": "ready", "yaml_config": "workflow:\n  name: test"}""")]
            }

        mock_agent.astream_events = mock_astream_events
        mock_agent.ainvoke = AsyncMock(side_effect=mock_ainvoke_impl)
        mock_create_agent.return_value = mock_agent

        asyncio.run(engine.run_async("task"))

        # Verify repair was attempted (ainvoke called)
        assert mock_agent.ainvoke.called


def test_engine_yaml_validation_repair(engine):
    with (
        patch("lego_agent.engine.create_react_agent") as mock_create_agent,
        patch("lego_agent.engine.build_llm"),
    ):
        mock_agent = MagicMock()

        # First call: Valid JSON, Invalid YAML (missing workflow)
        # Second call (repair): Valid YAML

        first_call_done = False

        async def mock_astream_events(*args, **kwargs):
            nonlocal first_call_done
            if not first_call_done:
                first_call_done = True
                yield {
                    "event": "on_chat_model_stream",
                    "data": {
                        "chunk": MagicMock(
                            content=r"""
                    {
                        "status": "ready",
                        "yaml_config": "invalid: yaml"
                    }
                    """
                        )
                    },
                }
            else:
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": MagicMock(content="Should not be here")},
                }

        async def mock_ainvoke_impl(*args, **kwargs):
            # This is the repair call
            return {
                "messages": [
                    MagicMock(content=r"""{"status": "ready", "yaml_config": "workflow:\n  name: repaired"}""")
                ]
            }

        mock_agent.astream_events = mock_astream_events
        mock_agent.ainvoke = AsyncMock(side_effect=mock_ainvoke_impl)
        mock_create_agent.return_value = mock_agent

        result = asyncio.run(engine.run_async("task"))

        # Verify repair was attempted
        assert mock_agent.ainvoke.called

        # Verify the config file contains the repaired content
        config_content = result.config_path.read_text()
        assert "name: repaired" in config_content
        # result.config_path points to file with yaml.
        # But we can check result.script_text? No, script_text is the launcher.
        # We can just assert success.
        assert result.script_path is not None
