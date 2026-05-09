import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app_operator.core import AgentConfig, Config, OperatorConfig
from lego_agent.backend.engine import LegoAgentEngine
from lego_agent.backend.io import UserIO
from lego_agent.prompts import get_loader
from libs.model_config import ModelConfig


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
        agent=AgentConfig(backend="gemini", model_config=ModelConfig(provider="gemini", model="gemini-1.5-pro")),
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
        patch("lego_agent.backend.engine.create_react_agent") as mock_create_agent,
        patch("lego_agent.backend.engine.build_llm"),
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
        assert result.config_path.exists()
        assert result.script_path.exists()
        assert result.config_path.parent == result.script_path.parent
        assert result.clarifications == []


def test_engine_clarification_loop(engine, mock_io):
    # Sequence: Clarify -> Ready
    # Since create_react_agent is called in a loop, we need to provide different mocks or behavior for each call
    # Or better, the mock_astream_events can yield different things based on
    # the prompt or just sequential calls.

    with (
        patch("lego_agent.backend.engine.create_react_agent") as mock_create_agent,
        patch("lego_agent.backend.engine.build_llm"),
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
        assert "os.chdir(repo_root)" in result.script_text
        assert "run_yaml_v2(str(config_path))" in result.script_text


def test_engine_validation_failure_and_repair(engine):
    with (
        patch("lego_agent.backend.engine.create_react_agent") as mock_create_agent,
        patch("lego_agent.backend.engine.build_llm"),
    ):
        mock_agent = MagicMock()

        call_count = 0

        async def mock_astream_events(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": MagicMock(content="Not JSON")},
                }
            else:
                # Repair round: submit_response tool call with valid data
                yield {
                    "event": "on_tool_start",
                    "name": "submit_response",
                    "data": {"input": {"status": "ready", "yaml_config": "workflow:\n  name: repaired"}},
                }

        mock_agent.astream_events = mock_astream_events
        mock_create_agent.return_value = mock_agent

        result = asyncio.run(engine.run_async("task"))

        assert result.script_path is not None
        assert call_count == 2


def test_engine_yaml_validation_repair(engine):
    with (
        patch("lego_agent.backend.engine.create_react_agent") as mock_create_agent,
        patch("lego_agent.backend.engine.build_llm"),
    ):
        mock_agent = MagicMock()

        call_count = 0

        async def mock_astream_events(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
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
                # Repair round: submit_response tool call with valid YAML
                yield {
                    "event": "on_tool_start",
                    "name": "submit_response",
                    "data": {"input": {"status": "ready", "yaml_config": "workflow:\n  name: repaired"}},
                }

        mock_agent.astream_events = mock_astream_events
        mock_create_agent.return_value = mock_agent

        result = asyncio.run(engine.run_async("task"))

        assert call_count == 2
        config_content = result.config_path.read_text()
        assert "name: repaired" in config_content
        assert result.script_path is not None


def test_extract_yaml_block_strips_prose(engine):
    """LLM often prepends prose before workflow: — extract_yaml_block removes it."""
    yaml_with_prose = "Here is the YAML:\nworkflow:\n  type: agent"
    result = engine._extract_yaml_block(yaml_with_prose)
    assert result.startswith("workflow:")
    assert "Here is the YAML" not in result


def test_extract_yaml_block_strips_code_fence(engine):
    result = engine._extract_yaml_block("```yaml\nworkflow:\n  type: agent\n```")
    assert result.startswith("workflow:")
    assert "```" not in result


def test_extract_yaml_block_passthrough_clean_yaml(engine):
    clean = "workflow:\n  type: agent\n  name: test"
    assert engine._extract_yaml_block(clean) == clean


def test_repair_with_tool_use_only_response(engine):
    """Reproduces Error 2: repair LLM calls submit_response (tool use) with no text."""
    with (
        patch("lego_agent.backend.engine.create_react_agent") as mock_create_agent,
        patch("lego_agent.backend.engine.build_llm"),
    ):
        mock_agent = MagicMock()

        call_count = 0

        async def mock_astream_events(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First round: invalid YAML triggers repair
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": MagicMock(content="Here is YAML:\nworkflow:\n  name: test")},
                }
            else:
                # Repair round: LLM calls submit_response with no text preamble
                yield {
                    "event": "on_tool_start",
                    "name": "submit_response",
                    "data": {"input": {"status": "ready", "yaml_config": "workflow:\n  name: fixed"}},
                }

        mock_agent.astream_events = mock_astream_events
        mock_create_agent.return_value = mock_agent

        result = asyncio.run(engine.run_async("task"))
        assert result.script_path is not None
