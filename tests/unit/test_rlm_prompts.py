"""Unit tests for RLM prompt templates, render functions, and DSPy integration."""

import pytest

from app_operator.dspy_integration._field_mappings import get_output_field_name
from app_operator.dspy_integration.optimizer import PROMPT_PHASE_MAP, PROMPT_TO_TEMPLATE
from app_operator.dspy_integration.signatures import SIGNATURES
from app_operator.prompts import SEED_TEMPLATE_MAP, PromptLoader, reset_loader
from app_operator.prompts.rlm import render_fix_error_task_prompt

RLM_PROMPT_NAME = "rlm_deployer_fix_error"


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


class TestSeedTemplate:
    """The seed template renders and is registered."""

    def test_seed_registered(self):
        assert RLM_PROMPT_NAME in SEED_TEMPLATE_MAP

    def test_seed_renders(self):
        loader = PromptLoader()
        template = SEED_TEMPLATE_MAP[RLM_PROMPT_NAME]
        result = loader.render_template(template, {})
        assert len(result) > 0


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


# ---------------------------------------------------------------------------
# DSPy signature registration
# ---------------------------------------------------------------------------


class TestDSPySignature:
    """The RLM prompt is registered in the SIGNATURES dict."""

    def test_signature_registered(self):
        assert RLM_PROMPT_NAME in SIGNATURES

    def test_signature_has_output_field(self):
        sig = SIGNATURES[RLM_PROMPT_NAME]
        assert "rendered_prompt" in sig.output_fields


# ---------------------------------------------------------------------------
# Output field mapping
# ---------------------------------------------------------------------------


class TestOutputFieldMapping:
    """The RLM prompt maps to 'rendered_prompt' output."""

    def test_output_field_is_rendered_prompt(self):
        assert get_output_field_name(RLM_PROMPT_NAME) == "rendered_prompt"


# ---------------------------------------------------------------------------
# Optimizer phase/template maps
# ---------------------------------------------------------------------------


class TestOptimizerMaps:
    """The RLM prompt is in PROMPT_PHASE_MAP and PROMPT_TO_TEMPLATE."""

    def test_phase_map(self):
        assert RLM_PROMPT_NAME in PROMPT_PHASE_MAP
        assert PROMPT_PHASE_MAP[RLM_PROMPT_NAME] == "deployment"

    def test_template_map(self):
        assert RLM_PROMPT_NAME in PROMPT_TO_TEMPLATE
        assert PROMPT_TO_TEMPLATE[RLM_PROMPT_NAME] == "rlm/deployer_fix_error.jinja2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
