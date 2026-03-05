"""Property-based tests for app_operator.validation using hypothesis.

These tests encode invariants of pure validation functions and explore
edge cases that manual tests may miss.
"""

import pytest

# Try to import hypothesis, skip tests if not available
try:
    from hypothesis import assume, given, settings
    from hypothesis import strategies as st

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False

    def given(*args, **kwargs):
        return pytest.mark.skip(reason="hypothesis not installed")

    def assume(*args, **kwargs):
        pass

    class DummySettings:
        def __call__(self, *args, **kwargs):
            return pytest.mark.skip(reason="hypothesis not installed")

    class DummyStrategies:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    settings = DummySettings()
    st = DummyStrategies()

from app_operator.validation import (
    validate_field,
    validate_in,
    validate_non_empty_str,
    validate_non_negative,
    validate_positive,
    validate_range,
    validate_type,
)

pytestmark = pytest.mark.skipif(
    not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed - install with: uv add --dev hypothesis"
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
if HYPOTHESIS_AVAILABLE:
    # Values that are neither bool nor int (bool is a subclass of int)
    non_int_strategy = st.one_of(
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(),
        st.binary(),
        st.lists(st.integers()),
        st.none(),
    )

    # Pure integers (explicitly excluding bool)
    pure_int_strategy = st.integers().filter(lambda x: not isinstance(x, bool))
else:
    non_int_strategy = None
    pure_int_strategy = None


# ---------------------------------------------------------------------------
# validate_type
# ---------------------------------------------------------------------------


class TestValidateTypeProperties:
    """Property-based tests for validate_type."""

    @given(
        value=non_int_strategy,
        name=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=50, deadline=1000)
    def test_non_int_non_bool_always_raises_type_error(self, value, name):
        """Non-int, non-bool values always raise TypeError when int is expected."""
        assume(not isinstance(value, (int, bool)))
        with pytest.raises(TypeError):
            validate_type(value, name, int)

    @given(
        value=pure_int_strategy,
        name=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=50, deadline=1000)
    def test_integers_never_raise(self, value, name):
        """Integer values never raise when int is expected."""
        # Should not raise
        validate_type(value, name, int)

    @given(
        value=non_int_strategy,
        name=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=50, deadline=1000)
    def test_error_message_contains_field_name(self, value, name):
        """TypeError message always contains the field name."""
        assume(not isinstance(value, (int, bool)))
        assume(name)  # non-empty
        with pytest.raises(TypeError) as exc_info:
            validate_type(value, name, int)
        assert name in str(exc_info.value)

    @given(name=st.text(min_size=1, max_size=50))
    @settings(max_examples=50, deadline=1000)
    def test_nullable_allows_none(self, name):
        """nullable=True allows None without raising."""
        # Should not raise
        validate_type(None, name, int, nullable=True)

    @given(
        value=non_int_strategy,
        name=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=50, deadline=1000)
    def test_nullable_false_still_raises_for_non_int(self, value, name):
        """nullable=False does not suppress TypeError for non-int values."""
        assume(not isinstance(value, (int, bool)))
        assume(value is not None)
        with pytest.raises(TypeError):
            validate_type(value, name, int, nullable=False)


# ---------------------------------------------------------------------------
# validate_positive
# ---------------------------------------------------------------------------


class TestValidatePositiveProperties:
    """Property-based tests for validate_positive."""

    @given(value=st.integers(min_value=1) | st.floats(min_value=1e-10, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50, deadline=1000)
    def test_positive_values_never_raise(self, value):
        """Values strictly greater than 0 never raise."""
        assume(value > 0)
        validate_positive(value, "x")  # should not raise

    @given(value=st.integers(max_value=0) | st.floats(max_value=0.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50, deadline=1000)
    def test_non_positive_values_always_raise(self, value):
        """Values ≤ 0 always raise ValueError."""
        assume(value <= 0)
        with pytest.raises(ValueError, match="must be positive"):
            validate_positive(value, "x")


# ---------------------------------------------------------------------------
# validate_non_negative
# ---------------------------------------------------------------------------


class TestValidateNonNegativeProperties:
    """Property-based tests for validate_non_negative."""

    @given(value=st.integers(min_value=0) | st.floats(min_value=0.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50, deadline=1000)
    def test_non_negative_values_never_raise(self, value):
        """Values ≥ 0 never raise."""
        assume(value >= 0)
        validate_non_negative(value, "x")  # should not raise

    @given(value=st.integers(max_value=-1) | st.floats(max_value=-1e-10, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50, deadline=1000)
    def test_negative_values_always_raise(self, value):
        """Values < 0 always raise ValueError."""
        assume(value < 0)
        with pytest.raises(ValueError, match="must be non-negative"):
            validate_non_negative(value, "x")


# ---------------------------------------------------------------------------
# validate_range
# ---------------------------------------------------------------------------


class TestValidateRangeProperties:
    """Property-based tests for validate_range."""

    @given(
        lo=st.floats(min_value=-100, max_value=0, allow_nan=False, allow_infinity=False),
        hi=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
        value=st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, deadline=1000)
    def test_inclusive_range_matches_python_comparison(self, lo, hi, value):
        """Inclusive range check is equivalent to Python's lo <= value <= hi."""
        assume(lo <= hi)
        in_range = lo <= value <= hi
        if in_range:
            validate_range(value, "x", min_val=lo, max_val=hi)  # should not raise
        else:
            with pytest.raises(ValueError, match="must be in range"):
                validate_range(value, "x", min_val=lo, max_val=hi)

    @given(
        lo=st.floats(min_value=-100, max_value=0, allow_nan=False, allow_infinity=False),
        hi=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
        value=st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False),
        min_exclusive=st.booleans(),
        max_exclusive=st.booleans(),
    )
    @settings(max_examples=50, deadline=1000)
    def test_exclusive_flags_respected(self, lo, hi, value, min_exclusive, max_exclusive):
        """Exclusive boundary flags make the boundary itself invalid."""
        assume(lo < hi)
        below = (value < lo) if not min_exclusive else (value <= lo)
        above = (value > hi) if not max_exclusive else (value >= hi)
        in_range = not below and not above
        if in_range:
            validate_range(
                value,
                "x",
                min_val=lo,
                max_val=hi,
                min_exclusive=min_exclusive,
                max_exclusive=max_exclusive,
            )
        else:
            with pytest.raises(ValueError, match="must be"):
                validate_range(
                    value,
                    "x",
                    min_val=lo,
                    max_val=hi,
                    min_exclusive=min_exclusive,
                    max_exclusive=max_exclusive,
                )


# ---------------------------------------------------------------------------
# validate_in
# ---------------------------------------------------------------------------


class TestValidateInProperties:
    """Property-based tests for validate_in."""

    @given(
        data=st.data(),
        values=st.sets(st.integers(), min_size=1, max_size=20),
    )
    @settings(max_examples=50, deadline=1000)
    def test_member_never_raises(self, data, values):
        """Values that are in the set never raise."""
        value = data.draw(st.sampled_from(sorted(values)))
        validate_in(value, "x", values)  # should not raise

    @given(
        values=st.sets(st.integers(), min_size=1, max_size=20),
        value=st.integers(),
    )
    @settings(max_examples=50, deadline=1000)
    def test_non_member_always_raises(self, values, value):
        """Values not in the set always raise ValueError."""
        assume(value not in values)
        with pytest.raises(ValueError, match="Invalid"):
            validate_in(value, "x", values)


# ---------------------------------------------------------------------------
# validate_non_empty_str
# ---------------------------------------------------------------------------


class TestValidateNonEmptyStrProperties:
    """Property-based tests for validate_non_empty_str."""

    @given(value=st.text(alphabet=st.characters(whitelist_categories=("Zs",)), min_size=1))
    @settings(max_examples=50, deadline=1000)
    def test_whitespace_only_always_raises(self, value):
        """Whitespace-only strings always raise ValueError."""
        assume(not value.strip())
        with pytest.raises(ValueError, match="must be a non-empty string"):
            validate_non_empty_str(value, "x")

    @given(value=st.one_of(st.integers(), st.floats(allow_nan=False), st.binary(), st.none()))
    @settings(max_examples=50, deadline=1000)
    def test_non_strings_always_raise(self, value):
        """Non-string values always raise ValueError."""
        assume(not isinstance(value, str))
        with pytest.raises(ValueError, match="must be a non-empty string"):
            validate_non_empty_str(value, "x")

    @given(
        value=st.text(min_size=1).filter(lambda s: s.strip()),
    )
    @settings(max_examples=50, deadline=1000)
    def test_non_empty_strings_never_raise(self, value):
        """Non-empty (stripped) strings never raise."""
        validate_non_empty_str(value, "x")  # should not raise


# ---------------------------------------------------------------------------
# validate_field combinator
# ---------------------------------------------------------------------------


class TestValidateFieldProperties:
    """Property-based tests for the validate_field combinator."""

    @given(
        value=non_int_strategy,
        name=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=50, deadline=1000)
    def test_type_constraint_enforced(self, value, name):
        """Type constraint is enforced — non-int raises TypeError regardless of other flags."""
        assume(not isinstance(value, (int, bool)))
        assume(value is not None)
        with pytest.raises(TypeError):
            validate_field(value, name, int)

    @given(name=st.text(min_size=1, max_size=50))
    @settings(max_examples=50, deadline=1000)
    def test_nullable_short_circuits_downstream_checks(self, name):
        """nullable=True with None short-circuits positive/range checks."""
        # With nullable=True, None should not reach positive check
        validate_field(None, name, int, nullable=True, positive=True)  # should not raise

    @given(
        value=st.integers(min_value=1),
        name=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=50, deadline=1000)
    def test_both_type_and_value_constraints_enforced(self, value, name):
        """Both type and positive constraints are enforced together."""
        assume(value > 0)
        validate_field(value, name, int, positive=True)  # should not raise
