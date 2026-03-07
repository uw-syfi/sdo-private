"""Tests for app_operator_dspy.signatures."""

import dspy

from app_operator_dspy.signatures import (
    AnalyzeCodebase,
    AnalyzeHealthCheck,
    DiagnoseDeploymentFailure,
    GenerateDeployScript,
    GenerateHealthCheckScript,
)

ALL_SIGNATURES = [
    AnalyzeCodebase,
    GenerateDeployScript,
    GenerateHealthCheckScript,
    DiagnoseDeploymentFailure,
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

    def test_diagnose_failure_fields(self):
        assert "error_output" in DiagnoseDeploymentFailure.input_fields
        assert "diagnosis" in DiagnoseDeploymentFailure.output_fields
        assert "fix_plan" in DiagnoseDeploymentFailure.output_fields

    def test_analyze_health_check_fields(self):
        assert "health_output" in AnalyzeHealthCheck.input_fields
        assert "status" in AnalyzeHealthCheck.output_fields
        assert "remediation" in AnalyzeHealthCheck.output_fields
