import pytest
from unittest.mock import MagicMock

from app_operator.agentflow.engine import AgentflowEngine
from app_operator.agentflow.io import UserIO
from app_operator.prompts import get_loader
from tests.fixtures.agents import ConfigurableAgent


class MockIO(UserIO):
    def __init__(self, answers=None):
        self.answers = answers or []
        self.questions_asked = []
        self.info_messages = []

    def read_prompt(self) -> str:
        return "test prompt"

    def ask_questions(self, questions):
        self.questions_asked.extend(questions)
        return self.answers.pop(0) if self.answers else ["mock answer"] * len(questions)

    def prompt_int(self, label: str) -> int:
        return 10

    def info(self, message: str) -> None:
        self.info_messages.append(message)


def test_engine_happy_path(tmp_path):
    agent = ConfigurableAgent()
    # First response: ready directly
    agent.set_default_response("""
    ```json
    {
        "status": "ready",
        "python_script": "import sys\\nfrom app_operator.agentflow.runtime import *\\nif __name__ == '__main__':\\n    MAX_ITERATIONS = 5\\n    pass"
    }
    ```
    """)

    io = MockIO()
    loader = get_loader()
    output_dir = tmp_path / "output"

    engine = AgentflowEngine(
        agent=agent,
        prompt_loader=loader,
        io=io,
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=output_dir,
    )

    result = engine.run("do something")

    assert result.script_text is not None
    assert (output_dir / result.script_path.parent.name / "agentflow.py").exists()
    assert result.clarifications == []


def test_engine_clarification_loop(tmp_path):
    # Sequence: Clarify -> Ready
    # Since ConfigurableAgent is stateless regarding sequence, we can simulate by checking prompt content
    # or using a more complex mock.
    # Here I'll mock generate directly on a MagicMock wrapping
    # ConfigurableAgent or just use MagicMock agent.

    mock_agent = MagicMock()
    mock_agent.generate.side_effect = [
        # Round 1: Clarify
        """{"status": "clarify", "questions": ["Q1"]}""",
        # Round 2: Ready
        """
        {
            "status": "ready",
            "python_script": "import app_operator.agentflow.runtime\\nMAX_ITERATIONS = 5\\nif __name__ == '__main__': pass"
        }
        """,
    ]

    io = MockIO(answers=[["A1"]])
    loader = get_loader()

    engine = AgentflowEngine(
        agent=mock_agent,
        prompt_loader=loader,
        io=io,
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=tmp_path,
    )

    result = engine.run("task")

    assert len(io.questions_asked) == 1
    assert io.questions_asked[0] == "Q1"
    assert result.clarifications == [("Q1", "A1")]
    assert "MAX_ITERATIONS = 5" in result.script_text


def test_engine_validation_failure_and_repair(tmp_path):
    # This test is tricky because repair is just a re-prompt.
    # We can simulate invalid JSON first, then valid.

    mock_agent = MagicMock()
    mock_agent.generate.side_effect = [
        "Not JSON",  # Fails parsing -> Repair
        """{"status": "ready", "python_script": "import app_operator.agentflow.runtime\\nMAX_ITERATIONS = 5\\nif __name__ == '__main__': pass"}""",  # Repair success
    ]

    io = MockIO()
    loader = get_loader()

    engine = AgentflowEngine(
        agent=mock_agent,
        prompt_loader=loader,
        io=io,
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=tmp_path,
    )

    engine.run("task")

    assert mock_agent.generate.call_count == 2
    # Verify second call contained repair info
    args, _ = mock_agent.generate.call_args
    assert "Error parsing" in args[0]


def test_engine_script_validation_error(tmp_path):
    # Script missing MAX_ITERATIONS
    mock_agent = MagicMock()
    mock_agent.generate.return_value = """
    {
        "status": "ready",
        "python_script": "print('bad script')"
    }
    """

    io = MockIO()
    loader = get_loader()

    engine = AgentflowEngine(
        agent=mock_agent,
        prompt_loader=loader,
        io=io,
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=tmp_path,
    )

    with pytest.raises(ValueError, match="Script validation failed"):
        engine.run("task")
