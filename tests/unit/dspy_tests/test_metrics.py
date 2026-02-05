"""Tests for DSPy metrics."""

import pytest
from unittest.mock import Mock
from app_operator.dspy_integration.metrics import (
    DeploymentSuccessMetric,
    IterationEfficiencyMetric,
    TokenEfficiencyMetric,
    PredictionQualityMetric,
    CompositeMetric,
    _parse_judge_score,
    _extract_prediction_text,
    _extract_error_context,
)
from app_operator.dspy_integration.data_loader import TrajectoryExample


@pytest.fixture
def successful_example():
    """Create a successful trajectory example."""
    return TrajectoryExample(
        trajectory_file="/test/traj.json",
        run_id="test123",
        phase="deployment",
        call_id=1,
        prompt="Deploy app",
        response="Deployed successfully",
        success=True,
        iterations=2,
        tool_calls=[{"exit_code": 0}],
        duration_seconds=120.0,
        token_usage={"input": 500, "output": 300, "total": 800},
    )


@pytest.fixture
def failed_example():
    """Create a failed trajectory example."""
    return TrajectoryExample(
        trajectory_file="/test/traj.json",
        run_id="test123",
        phase="deployment",
        call_id=2,
        prompt="Deploy app",
        response="Deployment failed",
        success=False,
        iterations=5,
        tool_calls=[{"exit_code": 1}],
        duration_seconds=300.0,
        token_usage={"input": 1000, "output": 600, "total": 1600},
    )


class TestDeploymentSuccessMetric:
    """Tests for DeploymentSuccessMetric."""

    def test_successful_deployment(self, successful_example):
        """Test metric on successful deployment."""
        metric = DeploymentSuccessMetric()
        score = metric(successful_example, None)
        assert score == 1.0

    def test_failed_deployment(self, failed_example):
        """Test metric on failed deployment."""
        metric = DeploymentSuccessMetric()
        score = metric(failed_example, None)
        assert score == 0.0

    def test_success_from_tool_calls(self):
        """Test determining success from tool calls."""
        metric = DeploymentSuccessMetric()

        # Example without success field but with successful tool call
        class MockExample:
            tool_calls = [{"exit_code": 0}]

        score = metric(MockExample(), None)
        assert score == 1.0

        # Example with failed tool call
        class FailedExample:
            tool_calls = [{"exit_code": 1}]

        score = metric(FailedExample(), None)
        assert score == 0.0

    def test_unknown_status(self):
        """Test unknown status returns 0.5."""
        metric = DeploymentSuccessMetric()

        class UnknownExample:
            pass

        score = metric(UnknownExample(), None)
        assert score == 0.5


class TestIterationEfficiencyMetric:
    """Tests for IterationEfficiencyMetric."""

    def test_single_iteration_success(self):
        """Test perfect score for single iteration."""
        metric = IterationEfficiencyMetric(max_iterations=20)

        class Example:
            iterations = 1
            success = True

        score = metric(Example(), None)
        assert score == 1.0

    def test_multiple_iterations_success(self, successful_example):
        """Test score decreases with more iterations."""
        metric = IterationEfficiencyMetric(max_iterations=20)
        score = metric(successful_example, None)  # 2 iterations

        # Should be less than 1.0 but more than 0.0
        assert 0.0 < score < 1.0

    def test_max_iterations(self):
        """Test score at max iterations."""
        metric = IterationEfficiencyMetric(max_iterations=20)

        class Example:
            iterations = 20
            success = True

        score = metric(Example(), None)
        # Should be close to 0.0
        assert score < 0.1

    def test_failed_deployment_penalty(self, failed_example):
        """Test that failed deployments get penalized."""
        metric = IterationEfficiencyMetric(max_iterations=20)
        score = metric(failed_example, None)

        # Failed deployments get 50% penalty
        assert score < 0.5

    def test_zero_iterations(self):
        """Test zero iterations returns 0.0."""
        metric = IterationEfficiencyMetric()

        class Example:
            iterations = 0
            success = True

        score = metric(Example(), None)
        assert score == 0.0

    def test_no_iterations_field(self):
        """Test missing iterations field returns 0.0."""
        metric = IterationEfficiencyMetric()

        class Example:
            pass

        score = metric(Example(), None)
        assert score == 0.0


class TestTokenEfficiencyMetric:
    """Tests for TokenEfficiencyMetric."""

    def test_low_token_usage(self):
        """Test high efficiency with low token usage."""
        metric = TokenEfficiencyMetric(baseline_tokens=10000)

        class Example:
            token_usage = {"input": 100, "output": 100}
            success = True

        score = metric(Example(), None)
        # Low usage = high efficiency
        assert score > 0.9

    def test_high_token_usage(self, successful_example):
        """Test lower efficiency with high token usage."""
        metric = TokenEfficiencyMetric(baseline_tokens=500)
        score = metric(successful_example, None)  # 800 tokens total

        # Usage > baseline, so efficiency should be lower
        assert score < 0.5

    def test_no_token_usage(self):
        """Test neutral score when token usage is unavailable."""
        metric = TokenEfficiencyMetric()

        class Example:
            token_usage = None

        score = metric(Example(), None)
        assert score == 0.5

    def test_input_output_weights(self):
        """Test weighted token calculation."""
        metric = TokenEfficiencyMetric(
            baseline_tokens=1000,
            input_weight=0.3,
            output_weight=0.7,
        )

        class Example:
            token_usage = {"input": 100, "output": 100}
            success = True

        score = metric(Example(), None)
        # Weighted tokens: 100*0.3 + 100*0.7 = 100
        # Efficiency: 1.0 - (100/1000) = 0.9
        assert abs(score - 0.9) < 0.01

    def test_failed_deployment_penalty(self, failed_example):
        """Test failed deployments get penalized."""
        metric = TokenEfficiencyMetric(baseline_tokens=2000)
        score = metric(failed_example, None)

        # Failed deployments get 50% penalty
        # Even with moderate token usage, score should be penalized
        assert score < 0.5


class TestCompositeMetric:
    """Tests for CompositeMetric."""

    def test_default_weights(self):
        """Test initialization with default weights."""
        metric = CompositeMetric()
        assert metric.success_weight == 0.6
        assert metric.efficiency_weight == 0.25
        assert metric.token_weight == 0.15

    def test_weights_sum_validation(self):
        """Test that weights must sum to 1.0."""
        with pytest.raises(ValueError, match="Metric weights must sum to 1.0"):
            CompositeMetric(
                success_weight=0.5,
                efficiency_weight=0.3,
                token_weight=0.1,
            )

    def test_composite_returns_zero_for_none_prediction(self, successful_example):
        """CompositeMetric returns 0.0 when prediction is None."""
        metric = CompositeMetric()
        score = metric(successful_example, None)
        assert score == 0.0

    def test_perfect_score(self):
        """Test perfect score with ideal example."""
        metric = CompositeMetric(max_iterations=20, baseline_tokens=10000)

        class PerfectExample:
            success = True
            iterations = 1
            token_usage = {"input": 100, "output": 100}

        score = metric(PerfectExample(), "some output")
        # Should be very high (close to 1.0)
        assert score > 0.9

    def test_poor_score(self):
        """Test poor score with suboptimal example."""
        metric = CompositeMetric(max_iterations=5, baseline_tokens=500)

        class PoorExample:
            success = False
            iterations = 5
            token_usage = {"input": 1000, "output": 1000}

        score = metric(PoorExample(), "some output")
        # Should be very low
        assert score < 0.3

    def test_composite_calculation(self, successful_example):
        """Test weighted composite calculation."""
        metric = CompositeMetric(
            success_weight=0.6,
            efficiency_weight=0.25,
            token_weight=0.15,
            max_iterations=20,
            baseline_tokens=10000,
        )

        score = metric(successful_example, "some output")

        # Should be between 0 and 1
        assert 0.0 <= score <= 1.0

        # Success contributes most (0.6 weight)
        assert score > 0.5

    def test_custom_weights(self):
        """Test custom weight configuration."""
        metric = CompositeMetric(
            success_weight=0.5,
            efficiency_weight=0.3,
            token_weight=0.2,
        )

        assert metric.success_weight == 0.5
        assert metric.efficiency_weight == 0.3
        assert metric.token_weight == 0.2

    def test_custom_prediction_metric_is_called(self):
        """prediction_metric replaces DeploymentSuccessMetric in the composite."""
        custom = Mock(return_value=0.7)
        metric = CompositeMetric(prediction_metric=custom)

        class Ex:
            success = True
            iterations = 1
            token_usage = {"input": 100, "output": 100}

        score = metric(Ex(), "prediction")

        custom.assert_called_once()
        # 0.7*0.6 + efficiency*0.25 + token*0.15 — verify it ran and contributed
        assert 0.0 < score <= 1.0


class TestParseJudgeScore:
    """Tests for _parse_judge_score."""

    def test_clean_integer(self):
        assert _parse_judge_score("8") == 0.8

    def test_integer_embedded_in_text(self):
        assert _parse_judge_score("I'd rate this a 7 out of 10") == 0.7

    def test_no_integer_returns_neutral(self):
        assert _parse_judge_score("no score here") == 0.5

    def test_clamped_above_10(self):
        assert _parse_judge_score("15") == 1.0

    def test_zero(self):
        assert _parse_judge_score("0") == 0.0


class TestExtractHelpers:
    """Tests for _extract_prediction_text and _extract_error_context."""

    def test_extract_prediction_rendered_prompt(self):
        pred = Mock(spec=["rendered_prompt"])
        pred.rendered_prompt = "the prompt"
        assert _extract_prediction_text(pred) == "the prompt"

    def test_extract_prediction_fallback_to_str(self):
        pred = Mock(spec=[])  # no known fields
        assert _extract_prediction_text(pred) == str(pred)

    def test_extract_error_context_present(self):
        ex = Mock(spec=["error_context"])
        ex.error_context = "Connection refused"
        assert _extract_error_context(ex) == "Connection refused"

    def test_extract_error_context_missing(self):
        ex = Mock(spec=[])
        assert _extract_error_context(ex) == ""


class TestPredictionQualityMetric:
    """Tests for PredictionQualityMetric."""

    def test_returns_zero_for_none_prediction(self):
        metric = PredictionQualityMetric()
        assert metric(Mock(), None) == 0.0

    def test_calls_judge_and_parses_score(self):
        """Judge is invoked and score is parsed correctly."""
        mock_judge = Mock(return_value=Mock(score="8"))

        metric = PredictionQualityMetric()
        metric._judge = mock_judge  # bypass lazy init

        example = Mock(spec=["error_context"])
        example.error_context = "Connection refused"
        prediction = Mock(spec=["rendered_prompt"])
        prediction.rendered_prompt = "Fix the connection error."

        score = metric(example, prediction)

        assert score == 0.8
        mock_judge.assert_called_once_with(
            error_context="Connection refused",
            generated_prompt="Fix the connection error.",
        )

    def test_falls_back_to_neutral_on_judge_error(self):
        """Any exception from the judge returns 0.5."""
        mock_judge = Mock(side_effect=Exception("LM error"))

        metric = PredictionQualityMetric()
        metric._judge = mock_judge

        example = Mock(spec=["error_context"])
        example.error_context = "err"
        prediction = Mock(spec=["rendered_prompt"])
        prediction.rendered_prompt = "some prompt"

        assert metric(example, prediction) == 0.5

    def test_judge_lazy_init(self):
        """Judge is not created until first call."""
        metric = PredictionQualityMetric()
        assert metric._judge is None  # not yet created
