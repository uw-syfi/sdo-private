"""Unit tests for sregym_agents.crucible.orchestrator helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from sregym_agents.crucible._prompts import _render
from sregym_agents.crucible.orchestrator import (
    _add_usage,
    _build_usage_result,
    _wait_for_mitigation_stage,
    _zero_usage,
)
from sregym_agents.crucible.tools import SharedFile

# ---------------------------------------------------------------------------
# _zero_usage
# ---------------------------------------------------------------------------


def test_zero_usage_shape():
    result = _zero_usage()
    assert result == {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}


# ---------------------------------------------------------------------------
# _add_usage
# ---------------------------------------------------------------------------


class TestAddUsage:
    def test_adds_corresponding_keys(self):
        a = {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 2}
        b = {"input_tokens": 3, "output_tokens": 7, "cached_input_tokens": 1}
        result = _add_usage(a, b)
        assert result == {"input_tokens": 13, "output_tokens": 12, "cached_input_tokens": 3}

    def test_missing_keys_in_b_treated_as_zero(self):
        a = {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 2}
        b = {"input_tokens": 3}
        result = _add_usage(a, b)
        assert result == {"input_tokens": 13, "output_tokens": 5, "cached_input_tokens": 2}


# ---------------------------------------------------------------------------
# _build_usage_result
# ---------------------------------------------------------------------------


class TestBuildUsageResult:
    def test_aggregates_total_correctly(self):
        usage_by_agent = {
            "diagnosis-agent": {
                "iterations": [],
                "total": {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 1},
            },
            "diagnosis-judge": {
                "iterations": [],
                "total": {"input_tokens": 20, "output_tokens": 8, "cached_input_tokens": 0},
            },
        }
        result = _build_usage_result(usage_by_agent)
        assert result["total"]["input_tokens"] == 30
        assert result["total"]["output_tokens"] == 13
        assert result["total"]["cached_input_tokens"] == 1

    def test_returns_nested_shape(self):
        usage_by_agent = {
            "agent": {"iterations": [], "total": _zero_usage()},
        }
        result = _build_usage_result(usage_by_agent)
        assert "by_agent" in result
        assert "total" in result
        assert result["by_agent"] == usage_by_agent


# ---------------------------------------------------------------------------
# SharedFile.init (orchestrator init content)
# ---------------------------------------------------------------------------


class TestSharedFileInit:
    def _init(self, shared_file, app_info: dict) -> None:
        SharedFile(shared_file).init(
            "# SRE Judged Session State\n"
            "## Session\n"
            f"- App: {app_info.get('app_name', 'unknown')} "
            f"/ Namespace: {app_info.get('namespace', 'default')}\n\n"
            "## Diagnosis\n"
        )

    def test_creates_parent_dirs(self, tmp_path: Path):
        shared = tmp_path / "sub" / "dir" / "session.md"
        self._init(shared, {"app_name": "myapp", "namespace": "ns"})
        assert shared.exists()

    def test_writes_header_with_app_and_namespace(self, tmp_path: Path):
        shared = tmp_path / "session.md"
        self._init(shared, {"app_name": "myapp", "namespace": "prod"})
        content = shared.read_text()
        assert "myapp" in content
        assert "prod" in content

    def test_file_readable_after_call(self, tmp_path: Path):
        shared = tmp_path / "session.md"
        self._init(shared, {})
        content = shared.read_text()
        assert len(content) > 0


# ---------------------------------------------------------------------------
# _wait_for_mitigation_stage
# ---------------------------------------------------------------------------


class TestWaitForMitigationStage:
    def test_returns_immediately_when_stage_is_mitigation(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"stage": "mitigation"}
        mock_resp.raise_for_status = MagicMock()

        with (
            patch("sregym_agents.crucible.orchestrator.requests.get", return_value=mock_resp),
            patch("sregym_agents.crucible.orchestrator.time.sleep") as mock_sleep,
            patch("sregym_agents.crucible.orchestrator.time.time", side_effect=[0.0, 1.0]),
        ):
            _wait_for_mitigation_stage("http://localhost:8000", timeout=300)

        mock_sleep.assert_not_called()

    def test_polls_until_stage_matches(self):
        responses = [
            MagicMock(**{"json.return_value": {"stage": "diagnosis"}, "raise_for_status": MagicMock()}),
            MagicMock(**{"json.return_value": {"stage": "diagnosis"}, "raise_for_status": MagicMock()}),
            MagicMock(**{"json.return_value": {"stage": "mitigation"}, "raise_for_status": MagicMock()}),
        ]
        # time.time called at: start, loop check 1, loop check 2, loop check 3
        times = [0.0, 1.0, 2.0, 3.0]

        with (
            patch("sregym_agents.crucible.orchestrator.requests.get", side_effect=responses),
            patch("sregym_agents.crucible.orchestrator.time.sleep"),
            patch("sregym_agents.crucible.orchestrator.time.time", side_effect=times),
        ):
            _wait_for_mitigation_stage("http://localhost:8000", timeout=300)

    def test_logs_warning_and_returns_on_timeout(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"stage": "diagnosis"}
        mock_resp.raise_for_status = MagicMock()

        # Use a callable side_effect so the mock never runs out of values.
        # First call (loop start) returns 0; all subsequent calls return 400 (past timeout).
        call_count = 0

        def fake_time():
            nonlocal call_count
            call_count += 1
            return 0.0 if call_count == 1 else 400.0

        with (
            patch("sregym_agents.crucible.orchestrator.requests.get", return_value=mock_resp),
            patch("sregym_agents.crucible.orchestrator.time.sleep"),
            patch("sregym_agents.crucible.orchestrator.time.time", side_effect=fake_time),
        ):
            # Should return without raising
            _wait_for_mitigation_stage("http://localhost:8000", timeout=300)

    def test_handles_requests_exception_gracefully(self):
        import requests as req

        call_count = 0
        times = [0.0, 1.0, 2.0, 3.0]

        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise req.exceptions.ConnectionError("refused")
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"stage": "mitigation"}
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with (
            patch("sregym_agents.crucible.orchestrator.requests.get", side_effect=fake_get),
            patch("sregym_agents.crucible.orchestrator.time.sleep"),
            patch("sregym_agents.crucible.orchestrator.time.time", side_effect=times),
        ):
            _wait_for_mitigation_stage("http://localhost:8000", timeout=300)


# ---------------------------------------------------------------------------
# _render lt_summary_file handling
# ---------------------------------------------------------------------------

_BASE_KWARGS = {
    "app_name": "app",
    "namespace": "ns",
    "descriptions": "",
    "iteration": 1,
    "shared_content": "",
    "shared_file": "/shared.md",
}


class TestRenderLtSummaryFile:
    @staticmethod
    def _render_both(lt_summary_file: str) -> list[str]:
        return [
            _render(tmpl, **_BASE_KWARGS, lt_summary_file=lt_summary_file)
            for tmpl in ("diagnosis_agent_user", "mitigation_agent_user")
        ]

    def test_empty_string_omits_summary_block(self):
        for rendered in self._render_both(""):
            assert "summary" not in rendered.lower()

    def test_path_includes_summary_block_and_path(self):
        for rendered in self._render_both("/path/to/summary.txt"):
            assert "/path/to/summary.txt" in rendered
