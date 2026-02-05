"""Tests for DSPy signatures."""

import pytest
from app_operator.dspy_integration.signatures import (
    get_signature,
    SIGNATURES,
    DeployerFixErrorSignature,
    AgentflowUserSignature,
)


class TestSignatures:
    """Tests for DSPy signature definitions."""

    def test_all_signatures_registered(self):
        """Test that all expected signatures are registered."""
        expected_prompts = {
            "deployer_system",
            "deployer_generate_script",
            "deployer_fix_error",
            "deployer_summarize",
            "code_analyzer_system",
            "code_analyzer_user",
            "monitor_analyze_health",
            "agentflow_system",
            "agentflow_user",
            "agentflow_repair",
        }
        assert set(SIGNATURES.keys()) == expected_prompts

    def test_get_signature_valid(self):
        """Test getting valid signatures."""
        sig = get_signature("deployer_fix_error")
        assert sig == DeployerFixErrorSignature

        sig = get_signature("agentflow_user")
        assert sig == AgentflowUserSignature

    def test_get_signature_invalid(self):
        """Test getting invalid signature raises KeyError."""
        with pytest.raises(KeyError, match="No signature found"):
            get_signature("nonexistent_prompt")

        with pytest.raises(KeyError, match="No signature found"):
            get_signature("")

    def test_get_signature_error_message(self):
        """Test error message includes available prompts."""
        try:
            get_signature("invalid")
        except KeyError as e:
            assert "Available prompts:" in str(e)
            assert "deployer_fix_error" in str(e)

    def test_deployer_fix_error_signature_fields(self):
        """Test DeployerFixErrorSignature has expected fields."""
        # Check fields exist in model_fields
        fields = DeployerFixErrorSignature.model_fields
        assert "repo_path" in fields
        assert "error_context" in fields
        assert "attempt" in fields
        assert "max_attempts" in fields
        assert "rendered_prompt" in fields

    def test_agentflow_user_signature_fields(self):
        """Test AgentflowUserSignature has expected fields."""
        # Check fields exist in model_fields
        fields = AgentflowUserSignature.model_fields
        assert "user_request" in fields
        assert "work_dir" in fields
        assert "loop_bound" in fields
        assert "script_code" in fields
        assert "status" in fields

    def test_signature_docstrings(self):
        """Test that all signatures have docstrings."""
        for prompt_name, signature in SIGNATURES.items():
            assert signature.__doc__ is not None, f"{prompt_name} missing docstring"
            assert len(signature.__doc__.strip()) > 0, f"{prompt_name} has empty docstring"

    def test_signature_count(self):
        """Test expected number of signatures."""
        assert len(SIGNATURES) == 10
