"""Tests for PromptReflector and ExecutionTrace."""

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from app_operator.config import GEPAConfig
from app_operator.gepa.reflector import ExecutionTrace, PromptReflector


@pytest.fixture
def reflector():
    """Create a PromptReflector with default config."""
    return PromptReflector(GEPAConfig())


class TestPromptReflectorFormatTraces:
    """Test _format_traces() method."""

    def test_single_trace(self, reflector):
        trace = ExecutionTrace(
            prompt_used="test",
            agent_type="deployer",
            phase="deployment",
            messages=[
                {"role": "user", "content": "deploy this"},
                {"role": "assistant", "content": "deploying..."},
            ],
            evaluation_result={"score": 0.5},
            success=False,
        )
        result = reflector._format_traces([trace])
        assert "deployer" in result
        assert "deployment" in result
        assert "False" in result

    def test_tool_call_formatting_includes_stdout(self, reflector):
        trace = ExecutionTrace(
            prompt_used="test",
            agent_type="deployer",
            phase="deployment",
            messages=[
                {
                    "role": "tool_call",
                    "tool": "bash",
                    "exit_code": 1,
                    "stdout": "Building project...",
                    "stderr": "command not found",
                },
            ],
            evaluation_result={},
            success=False,
        )
        result = reflector._format_traces([trace])
        assert "bash" in result
        assert "exit=1" in result
        assert "command not found" in result
        assert "Building project" in result


class TestPromptReflectorFormatMetricScores:
    """Test _format_metric_scores() method."""

    def test_with_scores(self, reflector):
        trace = ExecutionTrace(
            prompt_used="", agent_type="deployer", phase="deployment",
            messages=[], evaluation_result={}, success=True,
            metric_scores={"script_completeness": 0.70, "deployment_progress": 0.30},
        )
        result = reflector._format_metric_scores([trace])
        assert "Metric Scores" in result
        assert "script_completeness" in result
        assert "0.70" in result
        assert "Overall" in result


class TestPromptReflectorFormatGeneratedScripts:
    """Test _format_generated_scripts() method."""

    def test_with_scripts(self, reflector):
        trace = ExecutionTrace(
            prompt_used="", agent_type="deployer", phase="deployment",
            messages=[], evaluation_result={}, success=True,
            generated_scripts={"deploy.sh": "#!/bin/bash\necho hello"},
        )
        result = reflector._format_generated_scripts([trace])
        assert "Generated Scripts" in result
        assert "deploy.sh" in result
        assert "#!/bin/bash" in result


class TestPromptReflectorMutationGuidelines:
    """Test _mutation_guidelines() static method."""

    @pytest.mark.parametrize(
        "agent_type,expected_substr",
        [
            ("deployer", "Deployer"),
            ("monitor", "Monitor"),
            ("code_analyzer", "Code Analyzer"),
        ],
    )
    def test_known_agents_return_guidelines(self, agent_type, expected_substr):
        result = PromptReflector._mutation_guidelines(agent_type)
        assert expected_substr in result


class TestPromptReflectorBuildPrompts:
    """Test prompt building methods."""

    def test_reflection_prompt_structure(self, reflector):
        """Reflection prompt includes template name, current prompt, Jinja2 warning,
        guidelines, and metric scores."""
        trace = ExecutionTrace(
            prompt_used="", agent_type="deployer", phase="deployment",
            messages=[], evaluation_result={}, success=True,
            metric_scores={"test_metric": 0.42},
        )
        result = reflector._build_reflection_prompt(
            "my special prompt text", [trace], "deployer/system.jinja2"
        )
        assert "deployer/system.jinja2" in result
        assert "my special prompt text" in result
        assert "Jinja2" in result
        assert "Mutation Guidelines" in result
        assert "Metric Scores" in result
        assert "0.42" in result

    def test_crossover_prompt_includes_both_prompts(self, reflector):
        result = reflector._build_crossover_prompt(
            "prompt A text",
            "prompt B text",
            [],
            [],
            "deployer/system.jinja2",
        )
        assert "prompt A text" in result
        assert "prompt B text" in result
        assert "Prompt A" in result
        assert "Prompt B" in result


class TestCallReflectionLM:
    """Test _call_reflection_lm() response parsing."""

    def _make_reflector_with_stub(self, content):
        """Create a reflector whose LLM returns *content*."""
        reflector = PromptReflector(GEPAConfig())
        mock_llm = MagicMock()

        @dataclass
        class FakeResponse:
            content: object

        mock_llm.invoke.return_value = FakeResponse(content=content)
        reflector._llm = mock_llm
        return reflector

    def test_parses_string_response(self):
        text = (
            "<rationale>fix docker</rationale>\n"
            "<mutated_prompt>Deploy on {{ platform }} v2</mutated_prompt>"
        )
        reflector = self._make_reflector_with_stub(text)
        prompt, rationale = reflector._call_reflection_lm("ignored")
        assert prompt == "Deploy on {{ platform }} v2"
        assert rationale == "fix docker"

    def test_parses_list_of_parts_response(self):
        """Gemini thinking models return content as list of dicts."""
        parts = [
            {"type": "thinking", "text": "let me think..."},
            {"type": "text", "text": "<rationale>r</rationale>\n"
             "<mutated_prompt>result</mutated_prompt>"},
        ]
        reflector = self._make_reflector_with_stub(parts)
        prompt, rationale = reflector._call_reflection_lm("ignored")
        assert prompt == "result"
        assert rationale == "r"

    def test_missing_mutated_prompt_tag_raises(self):
        reflector = self._make_reflector_with_stub(
            "Here is my analysis but I forgot the tags."
        )
        with pytest.raises(ValueError, match="missing <mutated_prompt>"):
            reflector._call_reflection_lm("ignored")
