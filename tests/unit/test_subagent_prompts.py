"""Unit tests for subagent prompt templates and render functions."""

import pytest

from app_operator.prompts import PromptLoader, reset_loader
from app_operator.prompts.subagent import (
    render_error_log_analyst_prompt,
    render_repo_analyst_prompt,
    render_root_synthesis_prompt,
    render_script_analyst_prompt,
    render_trajectory_analyst_prompt,
)

BASELINE_TEMPLATES = [
    "subagent/trajectory_analyst.jinja2",
    "subagent/error_log_analyst.jinja2",
    "subagent/script_analyst.jinja2",
    "subagent/repo_analyst.jinja2",
    "subagent/root_synthesis.jinja2",
]


@pytest.fixture(autouse=True)
def _reset_loader():
    """Reset global loader before each test."""
    reset_loader()
    yield
    reset_loader()


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class TestBaselineTemplates:
    """All 5 baseline templates render without error."""

    @pytest.mark.parametrize("template", BASELINE_TEMPLATES)
    def test_baseline_renders(self, template):
        loader = PromptLoader()
        result = loader.render_template(template, {})
        assert len(result) > 0

    @pytest.mark.parametrize("template", BASELINE_TEMPLATES)
    def test_baseline_contains_expected_content(self, template):
        loader = PromptLoader()
        result = loader.render_template(template, {})
        # Each baseline should mention a role or instruction
        assert any(word in result.lower() for word in ["analyst", "deployment", "fix", "agent"]), (
            f"Template {template} missing expected content"
        )


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------


class TestRenderFunctions:
    """Each render function returns a non-empty string."""

    def test_render_trajectory_analyst(self):
        result = render_trajectory_analyst_prompt(data_description="test data")
        assert len(result) > 0
        assert "trajectory" in result.lower()

    def test_render_error_log_analyst(self):
        result = render_error_log_analyst_prompt(data_description="test data")
        assert len(result) > 0
        assert "error" in result.lower()

    def test_render_script_analyst(self):
        result = render_script_analyst_prompt(has_original_script="True")
        assert len(result) > 0
        assert "script" in result.lower()

    def test_render_repo_analyst(self):
        result = render_repo_analyst_prompt(available_files="Dockerfile")
        assert len(result) > 0
        assert "repo" in result.lower()

    def test_render_root_synthesis(self):
        result = render_root_synthesis_prompt(num_analysts="4")
        assert len(result) > 0
        assert "deploy.sh" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
