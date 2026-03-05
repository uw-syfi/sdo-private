"""Tests for DSPy metrics."""

from unittest.mock import Mock

import pytest

from app_operator.dspy_integration.data_loader import TrajectoryExample
from app_operator.dspy_integration.metrics import (
    CompositeMetric,
    DeploymentSuccessMetric,
    HealthCheckQualityMetric,
    IterationEfficiencyMetric,
    PredictionQualityMetric,
    TokenEfficiencyMetric,
    _extract_error_context,
    _extract_prediction_text,
    _parse_judge_score,
)


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
        health_check_script="""#!/bin/bash
curl http://localhost:8080
nc -z localhost 3306
docker compose ps
exit 0
""",
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
        health_check_script="#!/bin/bash\nexit 0\n",  # Trivial health check
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
        # Weighted tokens: 100 * 0.3 + 100 * 0.7 = 100 -> efficiency = 1.0 - 100/1000 = 0.9
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

    def test_default_weights_with_health_check(self):
        """Test initialization with default weights including health check."""
        metric = CompositeMetric()
        assert metric.success_weight == 0.5
        assert metric.efficiency_weight == 0.25
        assert metric.token_weight == 0.15
        assert metric.health_check_weight == 0.1
        assert metric.include_health_check_quality is True

    def test_disable_health_check_quality(self):
        """Test disabling health check quality validation (backward compatibility)."""
        metric = CompositeMetric(
            success_weight=0.6,
            efficiency_weight=0.25,
            token_weight=0.15,
            include_health_check_quality=False,
        )
        assert metric.health_check_weight == 0.0
        assert metric.health_check_metric is None
        assert metric.include_health_check_quality is False

    def test_weights_sum_validation_with_health_check(self):
        """Test that weights must sum to 1.0 when health check is enabled."""
        with pytest.raises(ValueError, match="Metric weights must sum to 1.0"):
            CompositeMetric(
                success_weight=0.5,
                efficiency_weight=0.3,
                token_weight=0.1,
                health_check_weight=0.05,  # Only sums to 0.95
            )

    def test_weights_sum_validation_without_health_check(self):
        """Test that weights validation works when health check is disabled."""
        with pytest.raises(ValueError, match="Metric weights .* must sum to 1.0"):
            CompositeMetric(
                success_weight=0.5,
                efficiency_weight=0.3,
                token_weight=0.1,  # Only sums to 0.9
                include_health_check_quality=False,
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
        metric = CompositeMetric(
            success_weight=0.6,
            efficiency_weight=0.25,
            token_weight=0.15,
            max_iterations=5,
            baseline_tokens=500,
            include_health_check_quality=False,
        )

        class PoorExample:
            success = False
            iterations = 5
            token_usage = {"input": 1000, "output": 1000}

        score = metric(PoorExample(), "some output")
        # Should be very low
        assert score < 0.3

    def test_health_check_quality_affects_score(self):
        """Test that health check quality is factored into composite score."""
        metric = CompositeMetric(
            success_weight=0.5,
            efficiency_weight=0.25,
            token_weight=0.15,
            health_check_weight=0.1,
            max_iterations=20,
            baseline_tokens=10000,
            include_health_check_quality=True,
        )

        # Example with good health check
        class GoodHealthCheck:
            success = True
            iterations = 2
            token_usage = {"input": 500, "output": 300}
            health_check_script = """#!/bin/bash
curl http://localhost:8080
nc -z localhost 3306
docker compose ps
redis-cli ping
mongo --eval "db.stats()"
exit 0
"""

        # Example with trivial health check (reward hacking)
        class TrivialHealthCheck:
            success = True
            iterations = 2
            token_usage = {"input": 500, "output": 300}
            health_check_script = "#!/bin/bash\nexit 0\n"

        score_good = metric(GoodHealthCheck(), "some output")
        score_trivial = metric(TrivialHealthCheck(), "some output")

        # Good health check should score higher
        assert score_good > score_trivial, (
            f"Good health check ({score_good}) should score higher than trivial ({score_trivial})"
        )

    def test_missing_health_check_neutral_impact(self):
        """Test that missing health check has neutral impact (0.5)."""
        metric = CompositeMetric(
            success_weight=0.5,
            efficiency_weight=0.25,
            token_weight=0.15,
            health_check_weight=0.1,
            include_health_check_quality=True,
        )

        class NoHealthCheck:
            success = True
            iterations = 1
            token_usage = {"input": 100, "output": 100}
            # No health_check_script attribute

        score = metric(NoHealthCheck(), "some output")
        # Should get base score + neutral (0.5) health check contribution
        # 0.5 * 1.0 + 0.25 * 1.0 + 0.15 * ~1.0 + 0.1 * 0.5 = ~0.95
        assert 0.90 <= score <= 1.0

    def test_composite_calculation(self, successful_example):
        """Test weighted composite calculation."""
        metric = CompositeMetric(
            success_weight=0.6,
            efficiency_weight=0.25,
            token_weight=0.15,
            max_iterations=20,
            baseline_tokens=10000,
            include_health_check_quality=False,
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
            include_health_check_quality=False,
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
        mock_judge = Mock(side_effect=RuntimeError("LM error"))

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


class TestHealthCheckQualityMetric:
    """Tests for HealthCheckQualityMetric to prevent reward hacking."""

    def test_trivial_exit_zero_script(self):
        """Trivial script with only 'exit 0' should get 0.0 score."""
        metric = HealthCheckQualityMetric()

        class MockExample:
            health_check_script = """#!/bin/bash
exit 0
"""

        score = metric(MockExample(), None)
        assert score == 0.0, "Trivial 'exit 0' script should score 0.0"

    def test_empty_script(self):
        """Empty script should get 0.0 score."""
        metric = HealthCheckQualityMetric()

        class MockExample:
            health_check_script = ""

        score = metric(MockExample(), None)
        assert score == 0.0

    def test_comprehensive_health_check(self):
        """Comprehensive health check script should get high score."""
        metric = HealthCheckQualityMetric()

        # Realistic health check script with multiple checks
        script = """#!/bin/bash
set -e

# Check docker
docker compose ps

# Check ports
nc -z localhost 8080
nc -z localhost 3306

# Check HTTP endpoints
curl -f http://localhost:8080/health
curl -f http://localhost:8081/status

# Check Redis
docker compose exec -T redis redis-cli ping

# Check MongoDB
docker compose exec -T mongodb mongo --eval "db.stats()"

# Check logs
docker compose logs --tail=50

exit 0
"""

        class MockExample:
            health_check_script = script

        score = metric(MockExample(), None)
        assert score >= 0.7, f"Comprehensive health check should score >= 0.7, got {score}"

    def test_partial_health_check(self):
        """Partial health check with some checks should get medium score."""
        metric = HealthCheckQualityMetric()

        script = """#!/bin/bash
# Basic health check

# Check if containers are running
docker compose ps

# Check main port
nc -z localhost 8080

echo "Health check passed"
exit 0
"""

        class MockExample:
            health_check_script = script

        score = metric(MockExample(), None)
        assert 0.3 <= score <= 0.7, f"Partial health check should score between 0.3-0.7, got {score}"

    def test_missing_health_check_script(self):
        """Missing health_check_script attribute should return neutral score."""
        metric = HealthCheckQualityMetric()

        class MockExample:
            pass

        score = metric(MockExample(), None)
        assert score == 0.5, "Missing health check script should return neutral 0.5"

    def test_none_health_check_script(self):
        """None health_check_script should return neutral score."""
        metric = HealthCheckQualityMetric()

        class MockExample:
            health_check_script = None

        score = metric(MockExample(), None)
        assert score == 0.5

    def test_count_check_commands(self):
        """Script with multiple check command types should score higher."""
        metric = HealthCheckQualityMetric()

        # Script with curl, nc, and docker
        script_multi = """#!/bin/bash
curl http://localhost:8080
nc -z localhost 3306
docker compose ps
exit 0
"""

        # Script with only curl
        script_single = """#!/bin/bash
curl http://localhost:8080
curl http://localhost:8081
curl http://localhost:8082
exit 0
"""

        class Example1:
            health_check_script = script_multi

        class Example2:
            health_check_script = script_single

        score_multi = metric(Example1(), None)
        score_single = metric(Example2(), None)

        # Multiple check types should score higher than single type
        assert score_multi > score_single, "Multiple check types should score higher than single type"

    def test_line_count_matters(self):
        """Scripts with more non-trivial lines should score higher."""
        metric = HealthCheckQualityMetric()

        # Short script (10 lines of actual code)
        short_script = """#!/bin/bash
# Health check
curl http://localhost:8080
curl http://localhost:8081
curl http://localhost:8082
curl http://localhost:8083
curl http://localhost:8084
curl http://localhost:8085
curl http://localhost:8086
curl http://localhost:8087
exit 0
"""

        # Long script (30+ lines of actual code)
        long_script = """#!/bin/bash
set -e
echo "Starting health check"

# Check containers
docker compose ps
docker compose ps --services --filter "status=running"

# Check ports
nc -z localhost 8080
nc -z localhost 8081
nc -z localhost 3306
nc -z localhost 6379
nc -z localhost 27017

# Check HTTP endpoints
curl -f http://localhost:8080/health
curl -f http://localhost:8081/status
curl -f http://localhost:8082/ready

# Check Redis
docker compose exec -T redis redis-cli ping
docker compose exec -T redis redis-cli info

# Check MongoDB
docker compose exec -T mongodb mongo --eval "db.stats()"
docker compose exec -T mongodb mongo --eval "db.version()"

# Check MySQL
docker compose exec -T mysql mysql -u root -ppassword -e "SELECT 1"

# Check logs
docker compose logs --tail=50
docker compose logs --tail=50 | grep -i error

echo "Health check completed successfully"
exit 0
"""

        class ShortExample:
            health_check_script = short_script

        class LongExample:
            health_check_script = long_script

        score_short = metric(ShortExample(), None)
        score_long = metric(LongExample(), None)

        # Longer scripts should generally score higher
        assert score_long > score_short, (
            f"Longer comprehensive script should score higher: {score_long} > {score_short}"
        )
