"""Tests for app_operator.progress module."""

from unittest.mock import patch

from app_operator.progress import emit_progress, parse_progress


class TestParseProgress:
    def test_parse_code_analysis(self):
        result = parse_progress("[SDS:PROGRESS] phase=code_analysis")
        assert result == {"phase": "code_analysis"}

    def test_parse_with_kwargs(self):
        result = parse_progress("[SDS:PROGRESS] phase=deployment attempt=2")
        assert result == {"phase": "deployment", "attempt": 2}

    def test_parse_monitoring_with_cycle(self):
        result = parse_progress("[SDS:PROGRESS] phase=monitoring cycle=3")
        assert result == {"phase": "monitoring", "cycle": 3}

    def test_parse_no_match_ordinary_line(self):
        assert parse_progress("INFO - Deployment Attempt #1") is None

    def test_parse_no_match_empty(self):
        assert parse_progress("") is None

    def test_parse_embedded_in_log_line(self):
        line = "2024-01-01 12:00:00 | INFO | [SDS:PROGRESS] phase=script_generation"
        result = parse_progress(line)
        assert result == {"phase": "script_generation"}


class TestEmitProgress:
    def test_emit_logs_marker_no_kwargs(self):
        with patch("app_operator.progress.logger") as mock_logger:
            emit_progress("code_analysis")
            mock_logger.info.assert_called_once_with("[SDS:PROGRESS] phase=code_analysis")

    def test_emit_logs_marker_with_kwargs(self):
        with patch("app_operator.progress.logger") as mock_logger:
            emit_progress("deployment", attempt=2)
            mock_logger.info.assert_called_once_with("[SDS:PROGRESS] phase=deployment attempt=2")

    def test_emit_multiple_kwargs(self):
        with patch("app_operator.progress.logger") as mock_logger:
            emit_progress("monitoring", cycle=3)
            call_args = mock_logger.info.call_args[0][0]
            assert "[SDS:PROGRESS] phase=monitoring" in call_args
            assert "cycle=3" in call_args

    def test_emit_then_parse_roundtrip(self):
        with patch("app_operator.progress.logger") as mock_logger:
            emit_progress("deployment", attempt=5)
            logged_line = mock_logger.info.call_args[0][0]
        result = parse_progress(logged_line)
        assert result == {"phase": "deployment", "attempt": 5}
