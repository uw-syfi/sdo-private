"""Unit tests for RLM (Recursive Language Model) components."""

import pytest
from pathlib import Path
from app_operator.rlm.environment import (
    RLMEnvironment,
    RLMContext,
    RLMCall,
    ActionType,
)
from app_operator.rlm.metrics import (
    RLMEfficiencyMetric,
    RLMContextUtilizationMetric,
    RLMCompositeMetric,
)


class TestRLMContext:
    """Tests for RLMContext dataclass."""

    def test_create_empty_context(self):
        """Test creating an empty context."""
        context = RLMContext()

        assert context.error_log == ""
        assert context.deployment_script == ""
        assert len(context.previous_attempts) == 0
        assert context.attempt_number == 0

    def test_create_context_with_data(self):
        """Test creating context with data."""
        context = RLMContext(
            error_log="Error: deployment failed",
            deployment_script="docker-compose up",
            previous_attempts=[{"attempt": 1, "error": "port conflict"}],
            attempt_number=2,
        )

        assert "deployment failed" in context.error_log
        assert "docker-compose" in context.deployment_script
        assert len(context.previous_attempts) == 1
        assert context.attempt_number == 2

    def test_to_dict(self):
        """Test converting context to dictionary."""
        context = RLMContext(error_log="test error", attempt_number=5)

        data = context.to_dict()

        assert data["error_log"] == "test error"
        assert data["attempt_number"] == 5
        assert isinstance(data, dict)

    def test_get_summary(self):
        """Test generating context summary."""
        context = RLMContext(
            error_log="x" * 10000, deployment_script="y" * 500, attempt_number=3
        )

        summary = context.get_summary()

        assert "10000 chars" in summary
        assert "500 chars" in summary
        assert "attempt_number: 3" in summary
        assert "Available context variables" in summary


class TestRLMEnvironment:
    """Tests for RLMEnvironment."""

    def test_create_environment(self):
        """Test creating RLM environment."""
        context = RLMContext(error_log="test log")
        env = RLMEnvironment(context)

        assert env.context == context
        assert env.max_recursion_depth == 5
        assert env.current_depth == 0
        assert len(env.call_history) == 0

    def test_execute_code_simple(self):
        """Test executing simple code."""
        context = RLMContext(error_log="Error: port 8080\nError: port 8081")
        env = RLMEnvironment(context)

        code = """
import re
errors = re.findall(r'Error: (.*)', error_log)
result = errors
"""

        result = env.execute_code(code, "Extract errors")

        assert "port 8080" in result
        assert "port 8081" in result
        assert len(env.call_history) == 1
        assert env.call_history[0].action_type == ActionType.EXECUTE_CODE

    def test_execute_code_with_utilities(self):
        """Test code execution has access to utilities."""
        context = RLMContext(error_log="line1\nline2\nline3")
        env = RLMEnvironment(context)

        code = """
lines = error_log.split('\\n')
result = len(lines)
"""

        result = env.execute_code(code, "Count lines")

        assert "3" in result

    def test_execute_code_failure(self):
        """Test code execution with invalid code."""
        context = RLMContext(error_log="test")
        env = RLMEnvironment(context)

        invalid_code = "result = undefined_variable"

        with pytest.raises(RuntimeError, match="Code execution failed"):
            env.execute_code(invalid_code, "This should fail")

        # Failed call still recorded
        assert len(env.call_history) == 1
        assert "ERROR" in env.call_history[0].output

    def test_recursive_call(self):
        """Test making recursive LLM calls."""
        context = RLMContext(error_log="test log")
        env = RLMEnvironment(context, max_recursion_depth=3)

        def mock_llm(prompt: str) -> str:
            return f"Response to: {prompt[:20]}"

        result = env.recursive_call(
            "Analyze this error", filtered_context={"snippet": "Error: test"}, llm_function=mock_llm
        )

        assert "Response to:" in result
        assert len(env.call_history) == 1
        assert env.call_history[0].action_type == ActionType.RECURSIVE_CALL
        # Depth incremented and decremented
        assert env.current_depth == 0

    def test_recursive_call_max_depth(self):
        """Test recursion depth limit."""
        context = RLMContext()
        env = RLMEnvironment(context, max_recursion_depth=2)

        def recursive_llm(prompt: str) -> str:
            if env.current_depth < 5:  # Try to exceed limit
                return env.recursive_call("sub-task", llm_function=recursive_llm)
            return "done"

        result = env.recursive_call("initial", llm_function=recursive_llm)

        # Should hit limit and return warning
        assert "limit" in result.lower() or "done" in result.lower()

    def test_get_statistics(self):
        """Test getting RLM statistics."""
        context = RLMContext(error_log="test" * 1000)
        env = RLMEnvironment(context)

        # Make some calls
        env.execute_code("result = len(error_log)", "Count chars")
        env.execute_code("result = 'test'", "Simple test")

        def mock_llm(p):
            return "response"

        env.recursive_call("subtask", llm_function=mock_llm)

        stats = env.get_statistics()

        assert stats["total_calls"] == 3
        assert stats["code_executions"] == 2
        assert stats["recursive_calls"] == 1
        assert stats["max_depth_reached"] == 1
        assert stats["total_tokens_saved"] > 0

    def test_record_callback(self):
        """Test that record callback is called."""
        context = RLMContext(error_log="test")
        recorded_calls = []

        def callback(call: RLMCall):
            recorded_calls.append(call)

        env = RLMEnvironment(context, record_callback=callback)

        env.execute_code("result = 42", "Test")

        assert len(recorded_calls) == 1
        assert recorded_calls[0].action_type == ActionType.EXECUTE_CODE

    def test_get_system_prompt(self):
        """Test RLM system prompt generation."""
        context = RLMContext(error_log="test log", attempt_number=5)
        env = RLMEnvironment(context)

        prompt = env.get_system_prompt()

        assert "Recursive Language Model" in prompt
        assert "execute_code" in prompt.lower()
        assert "recursive_call" in prompt.lower()
        assert "final_answer" in prompt.lower()
        assert "recursion depth: 0/5" in prompt.lower()


class TestRLMMetrics:
    """Tests for RLM metrics."""

    def test_rlm_efficiency_metric_good(self):
        """Test RLM efficiency metric with good example."""
        metric = RLMEfficiencyMetric(
            max_expected_calls=10, max_expected_depth=3, code_to_recursive_ratio_target=2.0
        )

        class MockExample:
            rlm_statistics = {
                "total_calls": 5,
                "code_executions": 4,
                "recursive_calls": 2,
                "max_depth_reached": 2,
            }
            success = True

        score = metric(MockExample(), None)

        # Should score well - reasonable calls, good ratio, shallow depth
        assert score > 0.7

    def test_rlm_efficiency_metric_poor(self):
        """Test RLM efficiency metric with poor example."""
        metric = RLMEfficiencyMetric()

        class MockExample:
            rlm_statistics = {
                "total_calls": 50,  # Too many calls
                "code_executions": 2,  # Not enough code
                "recursive_calls": 48,  # Too many recursive calls
                "max_depth_reached": 10,  # Too deep
            }
            success = False  # Also failed

        score = metric(MockExample(), None)

        # Should score poorly
        assert score < 0.3

    def test_rlm_efficiency_metric_no_rlm(self):
        """Test metric with non-RLM example."""
        metric = RLMEfficiencyMetric()

        class MockExample:
            pass  # No rlm_statistics

        score = metric(MockExample(), None)

        # Should return neutral score
        assert score == 0.5

    def test_rlm_context_utilization_metric_good(self):
        """Test context utilization metric with good example."""
        metric = RLMContextUtilizationMetric(target_savings_ratio=0.5)

        class MockExample:
            rlm_statistics = {"total_tokens_saved": 10000}
            error_log = "x" * 40000  # 10K tokens
            deployment_script = "y" * 10000  # 2.5K tokens
            previous_attempts = []

            # Total: ~12.5K tokens, saved 10K = 80% savings

        score = metric(MockExample(), None)

        # Exceeded target (50%), should score high
        assert score >= 1.0

    def test_rlm_context_utilization_metric_poor(self):
        """Test context utilization metric with poor example."""
        metric = RLMContextUtilizationMetric(target_savings_ratio=0.5)

        class MockExample:
            rlm_statistics = {"total_tokens_saved": 1000}
            error_log = "x" * 40000  # 10K tokens
            deployment_script = ""
            previous_attempts = []

            # Total: 10K tokens, saved 1K = 10% savings (below 50% target)

        score = metric(MockExample(), None)

        # Below target, should score low
        assert score < 0.5

    def test_rlm_composite_metric(self):
        """Test composite RLM metric."""
        metric = RLMCompositeMetric(
            success_weight=0.35,
            efficiency_weight=0.20,
            token_weight=0.15,
            rlm_efficiency_weight=0.15,
            rlm_context_weight=0.15,
        )

        class MockExample:
            success = True
            iterations = 3
            token_usage = {"input": 500, "output": 200}
            rlm_statistics = {
                "total_calls": 5,
                "code_executions": 3,
                "recursive_calls": 2,
                "total_tokens_saved": 8000,
                "max_depth_reached": 1,
            }
            error_log = "x" * 40000  # 10K tokens

        class MockPrediction:
            rendered_prompt = "test prompt"

        score = metric(MockExample(), MockPrediction())

        # Should score well - successful, efficient, good RLM usage
        assert score > 0.7
        assert score <= 1.0

    def test_rlm_composite_metric_weights_validation(self):
        """Test that composite metric validates weights sum to 1.0."""
        with pytest.raises(ValueError, match="must sum to 1.0"):
            RLMCompositeMetric(
                success_weight=0.5,
                efficiency_weight=0.5,  # Total > 1.0
                token_weight=0.2,
                rlm_efficiency_weight=0.1,
                rlm_context_weight=0.1,
            )

    def test_rlm_composite_metric_none_prediction(self):
        """Test composite metric with None prediction."""
        metric = RLMCompositeMetric()

        class MockExample:
            pass

        score = metric(MockExample(), None)

        # Should return 0.0 for None prediction
        assert score == 0.0


class TestRLMCall:
    """Tests for RLMCall dataclass."""

    def test_create_rlm_call(self):
        """Test creating an RLM call record."""
        call = RLMCall(
            action_type=ActionType.EXECUTE_CODE,
            depth=1,
            input_prompt="Extract errors",
            code_or_subtask="result = re.findall(...)",
            output="['Error 1', 'Error 2']",
            tokens_saved=5000,
            timestamp="2026-02-13 10:00:00",
        )

        assert call.action_type == ActionType.EXECUTE_CODE
        assert call.depth == 1
        assert call.tokens_saved == 5000

    def test_rlm_call_to_dict(self):
        """Test converting RLM call to dictionary."""
        call = RLMCall(
            action_type=ActionType.RECURSIVE_CALL,
            depth=2,
            input_prompt="Analyze ports",
            code_or_subtask='{"ports": [8080, 8081]}',
            output="Use ports 9080-9081",
        )

        data = call.to_dict()

        assert data["action_type"] == "recursive_call"
        assert data["depth"] == 2
        assert "ports" in data["code_or_subtask"]
        assert isinstance(data, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
