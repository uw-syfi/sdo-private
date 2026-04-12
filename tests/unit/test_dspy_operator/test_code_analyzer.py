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

    def test_forward_passes_repo_path(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM node:18")

        agent = CodeAnalyzerAgent()
        agent.analyze = MagicMock(return_value=dspy.Prediction(analysis="ok", issues="none"))

        agent.forward(str(tmp_path))

        call_kwargs = agent.analyze.call_args.kwargs
        assert call_kwargs["repo_path"] == str(tmp_path)
        assert "file_tree" not in call_kwargs
