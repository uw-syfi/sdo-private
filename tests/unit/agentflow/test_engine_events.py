import pytest
from unittest.mock import MagicMock
from agentflow.engine import AgentflowEngine
from agentflow.io import UserIO

class MockIO(UserIO):
    def __init__(self):
        self.info_messages = []
        self.stream_output = ""

    def read_prompt(self) -> str: return ""
    def ask_questions(self, questions): return []
    def prompt_int(self, label: str) -> int: return 0
    def info(self, message: str) -> None:
        self.info_messages.append(message)
    def print_stream(self, text: str) -> None:
        self.stream_output += text

def test_on_event_thinking(tmp_path):
    engine = AgentflowEngine(
        runner=MagicMock(),
        model="mock",
        prompt_loader=MagicMock(),
        io=MockIO(),
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=tmp_path,
        work_dir=tmp_path,
    )

    # Mock an event with a thought part
    part = MagicMock()
    part.text = None
    part.thought = "I am thinking"
    part.function_call = None
    part.function_response = None
    
    content = MagicMock()
    content.parts = [part]
    
    event = MagicMock()
    event.content = content
    event.get_function_calls.return_value = []
    event.get_function_responses.return_value = []

    engine._on_event(event)

    assert "\n[Thinking]" in engine.io.info_messages
    assert "I am thinking" in engine.io.stream_output
    assert engine._thinking_started is True

    # Send a text part now
    part2 = MagicMock()
    part2.text = "Hello world"
    part2.thought = None
    
    content2 = MagicMock()
    content2.parts = [part2]
    event2 = MagicMock()
    event2.content = content2
    
    engine._on_event(event2)
    assert "Hello world" in engine.io.stream_output
    assert engine._thinking_started is False

def test_on_event_tool_call(tmp_path):
    engine = AgentflowEngine(
        runner=MagicMock(),
        model="mock",
        prompt_loader=MagicMock(),
        io=MockIO(),
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=tmp_path,
        work_dir=tmp_path,
    )

    # Mock an event with a tool call via get_function_calls
    fn_call = MagicMock()
    fn_call.name = "my_tool"
    fn_call.args = {"arg1": "val1"}
    
    event = MagicMock()
    event.content = None
    event.get_function_calls.return_value = [fn_call]
    event.get_function_responses.return_value = []

    engine._on_event(event)

    print(f"DEBUG: info_messages: {engine.io.info_messages}")
    assert any("[Tool Use] my_tool" in msg and "arg1" in msg and "val1" in msg for msg in engine.io.info_messages)

def test_on_event_tool_response(tmp_path):
    engine = AgentflowEngine(
        runner=MagicMock(),
        model="mock",
        prompt_loader=MagicMock(),
        io=MockIO(),
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=tmp_path,
        work_dir=tmp_path,
    )

    # Mock an event with a tool response
    fn_resp = MagicMock()
    fn_resp.name = "my_tool"
    fn_resp.response = {"output": "tool success"}
    
    event = MagicMock()
    event.content = None
    event.get_function_calls.return_value = []
    event.get_function_responses.return_value = [fn_resp]

    engine._on_event(event)

    assert any("[Tool Result] my_tool: tool success" in msg for msg in engine.io.info_messages)
