"""Tests for app_operator_dspy.operator."""

from unittest.mock import MagicMock

import dspy

from app_operator_dspy.operator import DSPyOperator


def _mock_operator(deploy_success=True, monitor_statuses=None):
    if monitor_statuses is None:
        monitor_statuses = ["healthy"]

    op = DSPyOperator()
    op.analyzer = MagicMock(return_value=dspy.Prediction(analysis="# Analysis", issues="# Issues"))

    deploy_kwargs = {"success": deploy_success, "attempts": 1}
    if not deploy_success:
        deploy_kwargs["error"] = "deploy failed"
    op.deployer = MagicMock(return_value=dspy.Prediction(**deploy_kwargs))

    status_iter = iter(monitor_statuses)
    op.monitor = MagicMock(
        side_effect=lambda **kw: dspy.Prediction(status=next(status_iter), summary="ok", remediation="")
    )
    return op


class TestDSPyOperator:
    def test_is_dspy_module(self):
        op = DSPyOperator()
        assert isinstance(op, dspy.Module)

    def test_successful_run(self):
        op = _mock_operator(monitor_statuses=["healthy", "healthy"])
        result = op.forward("/app", monitor_checks=2)

        assert result.success is True
        assert result.phase == "monitoring"
        assert result.statuses == ["healthy", "healthy"]
        op.analyzer.assert_called_once()
        op.deployer.assert_called_once()
        assert op.monitor.call_count == 2

    def test_deployment_failure_skips_monitoring(self):
        op = _mock_operator(deploy_success=False)
        result = op.forward("/app")

        assert result.success is False
        assert result.phase == "deployment"
        op.monitor.assert_not_called()

    def test_monitoring_stops_on_unhealthy(self):
        op = _mock_operator(monitor_statuses=["healthy", "unhealthy"])
        result = op.forward("/app", monitor_checks=5)

        assert result.success is True
        assert result.statuses == ["healthy", "unhealthy"]
        assert op.monitor.call_count == 2
