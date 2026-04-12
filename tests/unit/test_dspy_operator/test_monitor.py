"""Tests for app_operator_dspy.agents.monitor."""

from unittest.mock import MagicMock, patch

import dspy
import pytest

from app_operator_dspy.agents.monitor import MonitorAgent


class TestMonitorAgent:
    def test_is_dspy_module(self):
        agent = MonitorAgent()
        assert isinstance(agent, dspy.Module)

    @patch("app_operator_dspy.agents.monitor.run_health_check")
    def test_forward_returns_analysis(self, mock_hc):
        mock_hc.return_value = "Exit code: 0\nStdout:\nAll services healthy"

        agent = MonitorAgent()
        agent.analyze = MagicMock(return_value=dspy.Prediction(status="healthy", summary="All good", remediation=""))

        result = agent.forward("/app", check_number=3)

        assert result.status == "healthy"
        assert result.remediation == ""
        agent.analyze.assert_called_once()

    @patch("app_operator_dspy.agents.monitor.run_health_check")
    def test_forward_passes_health_output(self, mock_hc):
        mock_hc.return_value = "Exit code: 1\nStderr:\ndb connection refused"

        agent = MonitorAgent()
        agent.analyze = MagicMock(
            return_value=dspy.Prediction(status="unhealthy", summary="DB down", remediation="restart db")
        )

        agent.forward("/app")

        call_kwargs = agent.analyze.call_args.kwargs
        assert "db connection refused" in call_kwargs["health_output"]
        assert call_kwargs["check_number"] == 1

    @patch("app_operator_dspy.agents.monitor.run_health_check")
    def test_status_normalized_to_lowercase(self, mock_hc):
        mock_hc.return_value = "ok"
        agent = MonitorAgent()
        agent.analyze = MagicMock(return_value=dspy.Prediction(status="HEALTHY", summary="fine", remediation=""))
        result = agent.forward("/app")
        assert result.status == "healthy"

    @patch("app_operator_dspy.agents.monitor.run_health_check")
    def test_invalid_status_raises(self, mock_hc):
        mock_hc.return_value = "ok"
        agent = MonitorAgent()
        agent.analyze = MagicMock(return_value=dspy.Prediction(status="partially healthy", summary="?", remediation=""))
        with pytest.raises(ValueError, match="status must be one of"):
            agent.forward("/app")
