import pytest
import asyncio

from agentflow.engine import AgentflowEngine
from agentflow.io import UserIO
from agentflow.prompts import get_loader


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


class MockAdkRunner:
    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_count = 0
        self.last_prompt = None

    async def run_async(self, agent, prompt, on_event=None):
        self.call_count += 1
        self.last_prompt = prompt
        if self.responses:
            return self.responses.pop(0)
        return ""


def test_engine_happy_path(tmp_path):
    runner = MockAdkRunner(
        responses=[
            """
        ```json
        {
            "status": "ready",
            "python_script": "import sys\\nfrom agentflow.runtime import *\\nif __name__ == '__main__':\\n    MAX_ITERATIONS = 5\\n    pass"
        }
        ```
        """
        ]
    )

    io = MockIO()
    loader = get_loader()
    output_dir = tmp_path / "output"

    engine = AgentflowEngine(
        runner=runner,
        model="mock-model",
        prompt_loader=loader,
        io=io,
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=output_dir,
        work_dir=tmp_path,
    )

    result = asyncio.run(engine.run_async("do something"))

    assert result.script_text is not None
    assert (output_dir / result.script_path.parent.name / "generated_script.py").exists()
    assert result.clarifications == []


def test_engine_clarification_loop(tmp_path):
    # Sequence: Clarify -> Ready
    runner = MockAdkRunner(
        responses=[
            """{"status": "clarify", "questions": ["Q1"]}""",
            """
        {
            "status": "ready",
            "python_script": "import agentflow.runtime\\nMAX_ITERATIONS = 5\\nif __name__ == '__main__': pass"
        }
        """,
        ]
    )

    io = MockIO(answers=[["A1"]])
    loader = get_loader()

    engine = AgentflowEngine(
        runner=runner,
        model="mock-model",
        prompt_loader=loader,
        io=io,
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=tmp_path,
        work_dir=tmp_path,
    )

    result = asyncio.run(engine.run_async("task"))

    assert len(io.questions_asked) == 1
    assert io.questions_asked[0] == "Q1"
    assert result.clarifications == [("Q1", "A1")]
    assert "MAX_ITERATIONS = 5" in result.script_text


def test_engine_validation_failure_and_repair(tmp_path):
    # This test is tricky because repair is just a re-prompt.
    runner = MockAdkRunner(
        responses=[
            "Not JSON",  # Fails parsing -> Repair
            """{"status": "ready", "python_script": "import agentflow.runtime\\nMAX_ITERATIONS = 5\\nif __name__ == '__main__': pass"}""",  # Repair success
        ]
    )

    io = MockIO()
    loader = get_loader()

    engine = AgentflowEngine(
        runner=runner,
        model="mock-model",
        prompt_loader=loader,
        io=io,
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=tmp_path,
        work_dir=tmp_path,
    )

    asyncio.run(engine.run_async("task"))

    assert runner.call_count == 2
    # Verify second call contained repair info
    assert "PARSING ERROR" in runner.last_prompt


def test_engine_script_validation_error(tmp_path):
    # Script missing MAX_ITERATIONS
    runner = MockAdkRunner(
        responses=[
            """
        {
            "status": "ready",
            "python_script": "print('bad script')"
        }
        """
        ]
    )

    io = MockIO()
    loader = get_loader()

    engine = AgentflowEngine(
        runner=runner,
        model="mock-model",
        prompt_loader=loader,
        io=io,
        loop_bound=5,
        max_clarifications=2,
        agent_timeout=1,
        output_dir=tmp_path,
        work_dir=tmp_path,
    )

    with pytest.raises(ValueError, match="Script validation failed"):
        asyncio.run(engine.run_async("task"))
