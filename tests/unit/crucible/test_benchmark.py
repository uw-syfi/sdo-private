"""Tests for the typed benchmark_result representation."""

from __future__ import annotations

import pytest

from libs.sregym_lib.benchmark import BenchmarkResult, Oracle


class TestBenchmarkResultRender:
    def test_success_with_oracle(self) -> None:
        result = BenchmarkResult(
            stage="diagnosis",
            success=True,
            message="ok",
            oracle=Oracle(stage="diagnosis", data={"Diagnosis": {"reasoning": "why"}}),
        )
        text = result.render()
        assert "<benchmark_result>" in text
        assert "success: True" in text
        assert "message: ok" in text
        assert "<oracle>" in text
        assert '"reasoning": "why"' in text
        assert text.endswith("</benchmark_result>\n")

    def test_success_without_oracle(self) -> None:
        result = BenchmarkResult(stage="diagnosis", success=True, message="ok", oracle=None)
        text = result.render()
        assert "success: True" in text
        assert "message: ok" in text
        assert "<oracle>" not in text
        # Two consecutive newlines where oracle_text would be
        assert "</benchmark_result>" in text

    def test_error_only(self) -> None:
        result = BenchmarkResult(stage="diagnosis", error="boom")
        text = result.render()
        assert "Error submitting to benchmark: boom" in text
        assert "success:" not in text
        assert "<oracle>" not in text
        assert "<benchmark_result>" in text


class TestBenchmarkResultParse:
    def test_returns_none_without_block(self) -> None:
        assert BenchmarkResult.parse("no tags here") is None

    def test_parses_success_with_oracle(self) -> None:
        block = (
            "<benchmark_result>\nsuccess: True\nmessage: accepted\n"
            '<oracle>\n{"Diagnosis": {"reasoning": "because"}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        parsed = BenchmarkResult.parse(block)
        assert parsed is not None
        assert parsed.success is True
        assert parsed.message == "accepted"
        assert parsed.oracle is not None
        assert parsed.oracle.reasoning == "because"

    def test_parses_success_without_oracle(self) -> None:
        block = "<benchmark_result>\nsuccess: True\nmessage: ok\n\n</benchmark_result>\n"
        parsed = BenchmarkResult.parse(block)
        assert parsed is not None
        assert parsed.success is True
        assert parsed.message == "ok"
        assert parsed.oracle is None

    def test_parses_error_block(self) -> None:
        block = "<benchmark_result>\nError submitting to benchmark: network down\n</benchmark_result>\n"
        parsed = BenchmarkResult.parse(block)
        assert parsed is not None
        assert parsed.error == "network down"
        assert parsed.success is False
        assert parsed.oracle is None

    def test_malformed_oracle_json_degrades_gracefully(self) -> None:
        block = (
            "<benchmark_result>\nsuccess: False\nmessage: oops\n<oracle>\nnot json\n</oracle>\n</benchmark_result>\n"
        )
        parsed = BenchmarkResult.parse(block)
        assert parsed is not None
        assert parsed.oracle is None

    def test_mitigation_stage_key(self) -> None:
        block = (
            "<benchmark_result>\nsuccess: True\nmessage: ok\n"
            '<oracle>\n{"Mitigation": {"reasoning": "mitigated"}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        parsed = BenchmarkResult.parse(block, stage="mitigation")
        assert parsed is not None
        assert parsed.oracle is not None
        assert parsed.oracle.reasoning == "mitigated"

    def test_unknown_stage_reasoning_is_empty(self) -> None:
        block = (
            "<benchmark_result>\nsuccess: True\nmessage: ok\n"
            '<oracle>\n{"Other": {"reasoning": "x"}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        parsed = BenchmarkResult.parse(block)
        assert parsed is not None
        assert parsed.oracle is not None
        assert parsed.oracle.reasoning == ""

    def test_multiline_reasoning(self) -> None:
        block = (
            "<benchmark_result>\nsuccess: False\nmessage: x\n"
            '<oracle>\n{\n  "Diagnosis": {\n    "reasoning": "multi\\nline"\n  }\n}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        parsed = BenchmarkResult.parse(block)
        assert parsed is not None
        assert parsed.oracle is not None
        assert parsed.oracle.reasoning == "multi\nline"

    def test_matched_candidate_index(self) -> None:
        block = (
            "<benchmark_result>\nsuccess: True\nmessage: ok\n"
            '<oracle>\n{"Diagnosis": {"matched_candidate_index": 2}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        parsed = BenchmarkResult.parse(block)
        assert parsed is not None
        assert parsed.oracle is not None
        assert parsed.oracle.matched_candidate_index == 2

    def test_matched_candidate_index_missing(self) -> None:
        block = (
            "<benchmark_result>\nsuccess: True\nmessage: ok\n"
            '<oracle>\n{"Diagnosis": {"reasoning": "x"}}\n</oracle>\n'
            "</benchmark_result>\n"
        )
        parsed = BenchmarkResult.parse(block)
        assert parsed is not None
        assert parsed.oracle is not None
        assert parsed.oracle.matched_candidate_index is None


class TestRoundTrip:
    @pytest.mark.parametrize(
        "result",
        [
            BenchmarkResult(
                stage="diagnosis",
                success=True,
                message="accepted",
                oracle=Oracle(
                    stage="diagnosis",
                    data={"Diagnosis": {"reasoning": "why", "matched_candidate_index": 1}},
                ),
            ),
            BenchmarkResult(stage="diagnosis", success=False, message="rejected", oracle=None),
            BenchmarkResult(stage="mitigation", error="explode"),
            BenchmarkResult(
                stage="mitigation",
                success=True,
                message="fixed",
                oracle=Oracle(stage="mitigation", data={"Mitigation": {"reasoning": "applied"}}),
            ),
        ],
    )
    def test_render_parse_round_trip(self, result: BenchmarkResult) -> None:
        rendered = result.render()
        parsed = BenchmarkResult.parse(rendered, stage=result.stage)
        assert parsed is not None
        assert parsed.success == result.success
        assert parsed.message == result.message
        assert parsed.error == result.error
        if result.oracle is None:
            assert parsed.oracle is None
        else:
            assert parsed.oracle is not None
            assert parsed.oracle.data == result.oracle.data
