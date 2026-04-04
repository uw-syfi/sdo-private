"""Property-based tests for app_operator.config dataclasses using hypothesis.

These tests complement the existing parametrized tests by systematically
exploring the input space of configuration validation logic.
"""

import pytest

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app_operator.config import (
    _FAULT_VALID_CATEGORIES,
    _FAULT_VALID_SEVERITIES,
    AgentConfig,
    DSPyOptimizationConfig,
    FaultInjectionConfig,
    OperatorConfig,
)

VALID_PROVIDERS = {
    "codex",
    "gemini",
    "claude",
    "claude-code",
    "opencode",
    "anthropic",
    "vertex",
    "openai",
    "rlm",
    "subagent",
    "hybrid",
}


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
@st.composite
def valid_metric_weights_strategy(draw):
    """Generate metric_weights dict with 4 keys summing to ~1.0."""
    # Draw 3 values in [0,1] and compute the 4th to ensure sum == 1.0
    a = draw(st.floats(min_value=0.0, max_value=0.97, allow_nan=False))
    b = draw(st.floats(min_value=0.0, max_value=1.0 - a, allow_nan=False))
    c = draw(st.floats(min_value=0.0, max_value=1.0 - a - b, allow_nan=False))
    d = round(1.0 - a - b - c, 10)
    assume(0.0 <= d <= 1.0)
    assume(0.99 <= a + b + c + d <= 1.01)
    return {
        "success": a,
        "efficiency": b,
        "tokens": c,
        "health_check": d,
    }


@st.composite
def invalid_sum_metric_weights_strategy(draw):
    """Generate metric_weights dict with 4 keys summing outside [0.99, 1.01]."""
    a = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
    b = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
    c = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
    d = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
    total = a + b + c + d
    assume(not (0.99 <= total <= 1.01))
    return {
        "success": a,
        "efficiency": b,
        "tokens": c,
        "health_check": d,
    }


@st.composite
def valid_fault_injection_args_strategy(draw):
    """Generate valid FaultInjectionConfig constructor arguments."""
    categories = draw(
        st.lists(
            st.sampled_from(sorted(_FAULT_VALID_CATEGORIES)),
            max_size=len(_FAULT_VALID_CATEGORIES),
            unique=True,
        )
    )
    severities = draw(
        st.lists(
            st.sampled_from(sorted(_FAULT_VALID_SEVERITIES)),
            max_size=len(_FAULT_VALID_SEVERITIES),
            unique=True,
        )
    )
    num_faults = draw(st.integers(min_value=1, max_value=5))
    return {
        "categories": categories,
        "severities": severities,
        "num_faults": num_faults,
    }


# ---------------------------------------------------------------------------
# AgentConfig
# ---------------------------------------------------------------------------


class TestAgentConfigProperties:
    """Property-based tests for AgentConfig provider validation."""

    @given(provider=st.sampled_from(sorted(VALID_PROVIDERS)))
    @settings(max_examples=50, deadline=1000)
    def test_valid_provider_always_succeeds(self, provider):
        """Every provider in VALID_PROVIDERS always constructs successfully."""
        config = AgentConfig(backend=provider)
        assert config.backend == provider.lower()

    @given(provider=st.text(min_size=1, max_size=50))
    @settings(max_examples=50, deadline=1000)
    def test_invalid_provider_always_raises_value_error(self, provider):
        """Any string not in VALID_PROVIDERS always raises ValueError."""
        assume(provider.lower() not in VALID_PROVIDERS)
        with pytest.raises(ValueError, match="Invalid backend"):
            AgentConfig(backend=provider)


# ---------------------------------------------------------------------------
# DSPyOptimizationConfig
# ---------------------------------------------------------------------------


class TestDSPyOptimizationConfigProperties:
    """Property-based tests for DSPyOptimizationConfig metric_weights validation."""

    @given(weights=valid_metric_weights_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_valid_weights_sum_accepted(self, weights):
        """metric_weights dict summing within [0.99, 1.01] is always accepted."""
        total = sum(weights.values())
        assume(0.99 <= total <= 1.01)
        config = DSPyOptimizationConfig(metric_weights=weights)
        assert config.metric_weights == weights

    @given(weights=invalid_sum_metric_weights_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_invalid_weights_sum_raises_value_error(self, weights):
        """metric_weights dict summing outside tolerance always raises ValueError."""
        with pytest.raises(ValueError, match="metric_weights must sum to"):
            DSPyOptimizationConfig(metric_weights=weights)


# ---------------------------------------------------------------------------
# FaultInjectionConfig
# ---------------------------------------------------------------------------


class TestFaultInjectionConfigProperties:
    """Property-based tests for FaultInjectionConfig validation."""

    @given(args=valid_fault_injection_args_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_valid_args_always_succeed(self, args):
        """Any valid subset of categories/severities and num_faults in [1,5] succeeds."""
        config = FaultInjectionConfig(**args)
        assert 1 <= config.num_faults <= 5

    @given(
        categories=st.lists(st.text(min_size=1, max_size=30), min_size=1, max_size=3),
    )
    @settings(max_examples=50, deadline=1000)
    def test_invalid_category_always_raises_value_error(self, categories):
        """Category strings not in valid set always raise ValueError."""
        assume(any(cat not in _FAULT_VALID_CATEGORIES for cat in categories))
        with pytest.raises(ValueError, match="Invalid.*categor"):
            FaultInjectionConfig(categories=categories)

    @given(num_faults=st.integers().filter(lambda n: n < 1 or n > 5))
    @settings(max_examples=50, deadline=1000)
    def test_num_faults_outside_range_raises_value_error(self, num_faults):
        """num_faults outside [1, 5] always raises ValueError."""
        with pytest.raises(ValueError, match="num_faults must be between"):
            FaultInjectionConfig(num_faults=num_faults)


# ---------------------------------------------------------------------------
# OperatorConfig
# ---------------------------------------------------------------------------


class TestOperatorConfigProperties:
    """Property-based tests for OperatorConfig interval validation."""

    @given(interval=st.integers(min_value=1, max_value=86400))
    @settings(max_examples=50, deadline=1000)
    def test_positive_interval_accepted(self, interval):
        """Any positive integer interval within the allowed range is always accepted."""
        config = OperatorConfig(interval=interval)
        assert config.interval == interval

    @given(interval=st.integers(max_value=0))
    @settings(max_examples=50, deadline=1000)
    def test_non_positive_interval_raises(self, interval):
        """Non-positive interval always raises ValueError."""
        with pytest.raises(ValueError, match="interval must be"):
            OperatorConfig(interval=interval)

    @given(interval=st.integers(min_value=86401))
    @settings(max_examples=50, deadline=1000)
    def test_oversized_interval_raises(self, interval):
        """interval > 86400 always raises ValueError."""
        with pytest.raises(ValueError, match="interval too large"):
            OperatorConfig(interval=interval)
