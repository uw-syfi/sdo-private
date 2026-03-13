"""Tests for app_operator_dspy.signatures."""

import dspy

from app_operator_dspy.signatures import (
    AnalyzeCodebase,
    AnalyzeHealthCheck,
    ConsolidateFixSummary,
    FixDeploymentError,
    GenerateDeployScript,
    GenerateHealthCheckScript,
)

ALL_SIGNATURES = [
    AnalyzeCodebase,
    GenerateDeployScript,
    GenerateHealthCheckScript,
    FixDeploymentError,
    ConsolidateFixSummary,
    AnalyzeHealthCheck,
]


class TestSignatureStructure:
    def test_all_are_dspy_signatures(self):
        for sig in ALL_SIGNATURES:
            assert issubclass(sig, dspy.Signature)

    def test_all_have_input_and_output_fields(self):
        for sig in ALL_SIGNATURES:
            assert len(sig.input_fields) > 0, f"{sig.__name__} has no input fields"
            assert len(sig.output_fields) > 0, f"{sig.__name__} has no output fields"

    def test_analyze_codebase_fields(self):
        assert "repo_path" in AnalyzeCodebase.input_fields
        assert "file_tree" in AnalyzeCodebase.input_fields
        assert "analysis" in AnalyzeCodebase.output_fields
        assert "issues" in AnalyzeCodebase.output_fields

    def test_fix_deployment_error_fields(self):
        assert "deploy_script" in FixDeploymentError.input_fields
        assert "error_output" in FixDeploymentError.input_fields
        assert "fix_history" in FixDeploymentError.input_fields
        assert "fixed_deploy_script" in FixDeploymentError.output_fields
        assert "fixed_health_check_script" in FixDeploymentError.output_fields
        assert "compose_override" in FixDeploymentError.output_fields
        assert "fix_summary" in FixDeploymentError.output_fields

    def test_consolidate_fix_summary_fields(self):
        assert "existing_summary" in ConsolidateFixSummary.input_fields
        assert "new_attempts" in ConsolidateFixSummary.input_fields
        assert "consolidated_summary" in ConsolidateFixSummary.output_fields

    def test_analyze_health_check_fields(self):
        assert "health_output" in AnalyzeHealthCheck.input_fields
        assert "status" in AnalyzeHealthCheck.output_fields
        assert "remediation" in AnalyzeHealthCheck.output_fields
