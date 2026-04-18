"""Tests for the shared benchmark oracle helpers."""

from __future__ import annotations

from sregym_agents.crucible._benchmark import extract_benchmark_reasoning


class TestExtractBenchmarkReasoning:
    def test_extracts_diagnosis_reasoning(self) -> None:
        block = (
            "<benchmark_result>\nsuccess: False\n"
            '<oracle>\n{"Diagnosis": {"reasoning": "the real cause"}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        assert extract_benchmark_reasoning(block) == "the real cause"

    def test_extracts_mitigation_reasoning(self) -> None:
        block = (
            "<benchmark_result>\nsuccess: False\n"
            '<oracle>\n{"Mitigation": {"reasoning": "apply the fix"}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        assert extract_benchmark_reasoning(block, stage="mitigation") == "apply the fix"

    def test_default_stage_is_diagnosis(self) -> None:
        block = '<oracle>\n{"Diagnosis": {"reasoning": "default"}}\n</oracle>'
        assert extract_benchmark_reasoning(block) == extract_benchmark_reasoning(block, stage="diagnosis")

    def test_empty_string_when_no_oracle(self) -> None:
        assert extract_benchmark_reasoning("no oracle here") == ""

    def test_empty_string_on_malformed_json(self) -> None:
        block = "<oracle>\nnot json\n</oracle>"
        assert extract_benchmark_reasoning(block) == ""

    def test_empty_string_when_key_absent(self) -> None:
        block = '<oracle>\n{"Other": {"reasoning": "x"}}\n</oracle>'
        assert extract_benchmark_reasoning(block) == ""

    def test_empty_string_when_reasoning_absent(self) -> None:
        block = '<oracle>\n{"Diagnosis": {"other_field": "x"}}\n</oracle>'
        assert extract_benchmark_reasoning(block) == ""

    def test_dotall_handles_multiline_json(self) -> None:
        block = '<oracle>\n{\n  "Diagnosis": {\n    "reasoning": "multi\\nline"\n  }\n}\n</oracle>'
        assert extract_benchmark_reasoning(block) == "multi\nline"
