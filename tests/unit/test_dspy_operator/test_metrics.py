"""Tests for app_operator_dspy.metrics."""

import dspy

from app_operator_dspy.metrics import deployment_metric, health_status_metric


class TestDeploymentMetric:
    def test_success_first_attempt(self):
        pred = dspy.Prediction(success=True, attempts=1)
        assert deployment_metric(None, pred) == 1.0

    def test_success_penalizes_extra_attempts(self):
        pred = dspy.Prediction(success=True, attempts=3)
        assert deployment_metric(None, pred) == 0.6

    def test_failure_returns_zero(self):
        pred = dspy.Prediction(success=False, attempts=1)
        assert deployment_metric(None, pred) == 0.0

    def test_none_prediction_returns_zero(self):
        assert deployment_metric(None, None) == 0.0

    def test_minimum_score_is_0_1(self):
        pred = dspy.Prediction(success=True, attempts=100)
        assert deployment_metric(None, pred) == 0.1


class TestHealthStatusMetric:
    def test_healthy(self):
        pred = dspy.Prediction(status="healthy")
        assert health_status_metric(None, pred) == 1.0

    def test_degraded(self):
        pred = dspy.Prediction(status="degraded")
        assert health_status_metric(None, pred) == 0.5

    def test_unhealthy(self):
        pred = dspy.Prediction(status="unhealthy")
        assert health_status_metric(None, pred) == 0.0

    def test_unknown_status(self):
        pred = dspy.Prediction(status="unknown")
        assert health_status_metric(None, pred) == 0.0
