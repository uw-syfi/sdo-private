"""Property-based tests for TrajectoryMessage.to_dict() using hypothesis.

These tests encode invariants about dictionary serialization of TrajectoryMessage,
ensuring correctness for all combinations of present/absent optional fields.
"""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from app_operator.trajectory import TrajectoryMessage

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
_optional_text = st.one_of(st.none(), st.text(max_size=200))
_optional_int = st.one_of(st.none(), st.integers(min_value=-1, max_value=255))
_optional_float = st.one_of(st.none(), st.floats(min_value=0.0, max_value=3600.0, allow_nan=False))
_optional_dict = st.one_of(
    st.none(),
    st.dictionaries(
        st.text(min_size=1, max_size=20),
        st.text(max_size=50),
        max_size=5,
    ),
)


@st.composite
def trajectory_message_strategy(draw):
    """Generate a TrajectoryMessage with all combinations of optional fields."""
    return TrajectoryMessage(
        role=draw(st.text(min_size=1, max_size=30)),
        content=draw(_optional_text),
        tool=draw(_optional_text),
        args=draw(_optional_dict),
        stdout=draw(_optional_text),
        stderr=draw(_optional_text),
        exit_code=draw(_optional_int),
        timestamp=draw(_optional_text),
        duration_seconds=draw(_optional_float),
    )


# ---------------------------------------------------------------------------
# TrajectoryMessage.to_dict() invariants
# ---------------------------------------------------------------------------


class TestTrajectoryMessageToDictProperties:
    """Property-based tests for TrajectoryMessage.to_dict()."""

    @given(msg=trajectory_message_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_no_none_values_in_output(self, msg):
        """to_dict() never contains keys with None values."""
        result = msg.to_dict()
        for key, value in result.items():
            assert value is not None, f"Key '{key}' has None value in to_dict() output"

    @given(msg=trajectory_message_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_non_none_fields_included(self, msg):
        """Every field set to a non-None value is present in to_dict() output."""
        result = msg.to_dict()
        if msg.content is not None:
            assert "content" in result
        if msg.tool is not None:
            assert "tool" in result
        if msg.args is not None:
            assert "args" in result
        if msg.stdout is not None:
            assert "stdout" in result
        if msg.stderr is not None:
            assert "stderr" in result
        if msg.exit_code is not None:
            assert "exit_code" in result
        if msg.timestamp is not None:
            assert "timestamp" in result
        if msg.duration_seconds is not None:
            assert "duration_seconds" in result

    @given(msg=trajectory_message_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_role_always_present(self, msg):
        """'role' key is always present in to_dict() output."""
        result = msg.to_dict()
        assert "role" in result

    @given(msg=trajectory_message_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_output_is_json_serializable(self, msg):
        """to_dict() output is always JSON-serializable (critical for file writing)."""
        result = msg.to_dict()
        # Should not raise
        json_str = json.dumps(result)
        assert isinstance(json_str, str)

    @given(msg=trajectory_message_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_role_value_matches_input(self, msg):
        """'role' value in to_dict() matches the role passed at construction."""
        result = msg.to_dict()
        assert result["role"] == msg.role
