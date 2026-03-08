"""Tests for app_operator_dspy.agents.code_analyzer."""

from unittest.mock import MagicMock

import dspy

from app_operator_dspy.agents.code_analyzer import CodeAnalyzerAgent


class TestCodeAnalyzerAgent:
    def test_is_dspy_module(self):
        agent = CodeAnalyzerAgent()
        assert isinstance(agent, dspy.Module)

    def test_forward_writes_output_files(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.12")
        (tmp_path / "app.py").write_text("print('hello')")

        agent = CodeAnalyzerAgent()
        agent.analyze = MagicMock(return_value=dspy.Prediction(analysis="# Analysis", issues="# Issues"))

        result = agent.forward(str(tmp_path))

        assert (tmp_path / ".sds" / "code_analysis.md").read_text() == "# Analysis"
        assert (tmp_path / ".sds" / "deployment_issues.md").read_text() == "# Issues"
        assert result.analysis == "# Analysis"
        assert result.issues == "# Issues"

    def test_forward_passes_file_contents(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM node:18")
        (tmp_path / "docker-compose.yml").write_text("version: '3'")

        agent = CodeAnalyzerAgent()
        agent.analyze = MagicMock(return_value=dspy.Prediction(analysis="ok", issues="none"))

        agent.forward(str(tmp_path))

        call_kwargs = agent.analyze.call_args.kwargs
        assert "FROM node:18" in call_kwargs["file_tree"]
        assert "version: '3'" in call_kwargs["file_tree"]

    def test_forward_returns_raw_context(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM node:18")

        agent = CodeAnalyzerAgent()
        agent.analyze = MagicMock(return_value=dspy.Prediction(analysis="ok", issues="none"))

        result = agent.forward(str(tmp_path))

        assert "FROM node:18" in result.raw_context

    def test_forward_works_without_files(self, tmp_path):
        agent = CodeAnalyzerAgent()
        agent.analyze = MagicMock(return_value=dspy.Prediction(analysis="a", issues="b"))

        result = agent.forward(str(tmp_path))

        assert result.analysis == "a"
        call_kwargs = agent.analyze.call_args.kwargs
        assert "File Contents" not in call_kwargs["file_tree"]

    def test_skips_large_files(self, tmp_path):
        (tmp_path / "small.txt").write_text("small")
        (tmp_path / "big.txt").write_text("x" * 200)

        agent = CodeAnalyzerAgent(max_file_size=100)
        agent.analyze = MagicMock(return_value=dspy.Prediction(analysis="a", issues="b"))

        agent.forward(str(tmp_path))

        call_kwargs = agent.analyze.call_args.kwargs
        assert "small" in call_kwargs["file_tree"]
        assert "x" * 200 not in call_kwargs["file_tree"]
