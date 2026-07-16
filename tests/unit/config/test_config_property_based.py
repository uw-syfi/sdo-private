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
    FaultInjectionConfig,
    OperatorConfig,
)

VALID_PROVIDERS = AgentConfig.VALID_BACKENDS


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
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
