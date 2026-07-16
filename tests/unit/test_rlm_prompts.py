"""Unit tests for RLM prompt templates and render functions."""

import pytest

from app_operator.prompts import PromptLoader, reset_loader
from app_operator.prompts.rlm import render_fix_error_task_prompt


@pytest.fixture(autouse=True)
def _reset_loader():
    """Reset global loader before each test."""
    reset_loader()
    yield
    reset_loader()


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class TestBaselineTemplate:
    """The baseline template renders without error."""

    def test_baseline_renders(self):
        loader = PromptLoader()
        result = loader.render_template("rlm/deployer_fix_error.jinja2", {})
        assert len(result) > 0

    def test_baseline_contains_expected_content(self):
        loader = PromptLoader()
        result = loader.render_template("rlm/deployer_fix_error.jinja2", {"available_specialists": "error_log, repo"})
        assert "validate_file_refs" in result
        assert "MANDATORY FIRST STEPS" in result
        assert "FINAL_ANSWER" in result
        assert "specialist_call" in result
        assert "chunking or filtering strategy" in result
        assert "REPL variables/buffers" in result
        assert "sub_rlm(...)" in result


# ---------------------------------------------------------------------------
# Render function
# ---------------------------------------------------------------------------


class TestRenderFunction:
    """The render function returns a non-empty string."""

    def test_render_fix_error_task_prompt(self):
        result = render_fix_error_task_prompt(
            repo_path="/tmp/repo",
            available_variables="error_log, deployment_script",
            error_log_size="5000",
            attempt="2",
            max_attempts="10",
            has_original_script="True",
            available_specialists="error_log, repo",
        )
        assert len(result) > 0
        assert "validate_file_refs" in result
        assert "specialist_call" in result

    def test_render_returns_string(self):
        result = render_fix_error_task_prompt()
        assert isinstance(result, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
