"""Unit tests for subagent prompt templates, render functions, and DSPy integration."""

import pytest

from app_operator.dspy_integration._field_mappings import get_output_field_name
from app_operator.dspy_integration.optimizer import PROMPT_PHASE_MAP, PROMPT_TO_TEMPLATE
from app_operator.dspy_integration.signatures import SIGNATURES
from app_operator.prompts import SEED_TEMPLATE_MAP, PromptLoader, reset_loader
from app_operator.prompts.subagent import (
    render_error_log_analyst_prompt,
    render_repo_analyst_prompt,
    render_root_synthesis_prompt,
    render_script_analyst_prompt,
    render_trajectory_analyst_prompt,
)

SUBAGENT_PROMPT_NAMES = [
    "subagent_trajectory_analyst",
    "subagent_error_log_analyst",
    "subagent_script_analyst",
    "subagent_repo_analyst",
    "subagent_root_synthesis",
]

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


class TestSeedTemplates:
    """All 5 seed templates render and are registered."""

    @pytest.mark.parametrize("prompt_name", SUBAGENT_PROMPT_NAMES)
    def test_seed_registered(self, prompt_name):
        assert prompt_name in SEED_TEMPLATE_MAP

    @pytest.mark.parametrize("prompt_name", SUBAGENT_PROMPT_NAMES)
    def test_seed_renders(self, prompt_name):
        loader = PromptLoader()
        template = SEED_TEMPLATE_MAP[prompt_name]
        result = loader.render_template(template, {})
        assert len(result) > 0


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


# ---------------------------------------------------------------------------
# DSPy signature registration
# ---------------------------------------------------------------------------


class TestDSPySignatures:
    """All 5 subagent prompts are registered in the SIGNATURES dict."""

    @pytest.mark.parametrize("prompt_name", SUBAGENT_PROMPT_NAMES)
    def test_signature_registered(self, prompt_name):
        assert prompt_name in SIGNATURES

    @pytest.mark.parametrize("prompt_name", SUBAGENT_PROMPT_NAMES)
    def test_signature_has_output_field(self, prompt_name):
        sig = SIGNATURES[prompt_name]
        assert "system_prompt" in sig.output_fields


# ---------------------------------------------------------------------------
# Output field mappings
# ---------------------------------------------------------------------------


class TestOutputFieldMappings:
    """All 5 subagent prompts map to 'system_prompt' output."""

    @pytest.mark.parametrize("prompt_name", SUBAGENT_PROMPT_NAMES)
    def test_output_field_is_system_prompt(self, prompt_name):
        assert get_output_field_name(prompt_name) == "system_prompt"


# ---------------------------------------------------------------------------
# Optimizer phase/template maps
# ---------------------------------------------------------------------------


class TestOptimizerMaps:
    """All 5 subagent prompts are in PROMPT_PHASE_MAP and PROMPT_TO_TEMPLATE."""

    @pytest.mark.parametrize("prompt_name", SUBAGENT_PROMPT_NAMES)
    def test_phase_map(self, prompt_name):
        assert prompt_name in PROMPT_PHASE_MAP
        assert PROMPT_PHASE_MAP[prompt_name] == "deployment"

    @pytest.mark.parametrize("prompt_name", SUBAGENT_PROMPT_NAMES)
    def test_template_map(self, prompt_name):
        assert prompt_name in PROMPT_TO_TEMPLATE
        assert PROMPT_TO_TEMPLATE[prompt_name].startswith("subagent/")
        assert PROMPT_TO_TEMPLATE[prompt_name].endswith(".jinja2")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
