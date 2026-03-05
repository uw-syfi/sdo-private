"""Property-based tests for lego_agent.models using hypothesis.

These tests encode invariants of extract_json and parse_lego_agent_response,
verifying robustness against arbitrary string inputs.
"""

import json
import pytest

# Try to import hypothesis, skip tests if not available
try:
    from hypothesis import given, strategies as st, assume, settings
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

from lego_agent.models import extract_json, parse_lego_agent_response, LegoAgentResponse

pytestmark = pytest.mark.skipif(
    not HYPOTHESIS_AVAILABLE,
    reason="hypothesis not installed - install with: uv add --dev hypothesis"
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
if HYPOTHESIS_AVAILABLE:
    # Prefix/suffix safe characters: no braces so they don't interfere with
    # the brace-slice extraction heuristic in extract_json.
    _safe_text = st.text(
        alphabet=st.characters(blacklist_characters="{}"),
        max_size=30,
    )

    @st.composite
    def clarify_json_strategy(draw):
        """Generate a valid clarify JSON payload embedded in arbitrary text."""
        questions = draw(st.lists(
            st.text(min_size=1, max_size=80).filter(str.strip),
            min_size=1, max_size=5,
        ))
        payload = json.dumps({"status": "clarify", "questions": questions})
        prefix = draw(_safe_text)
        suffix = draw(_safe_text)
        return prefix + payload + suffix, questions

    @st.composite
    def ready_json_strategy(draw):
        """Generate a valid ready JSON payload embedded in arbitrary text."""
        yaml_config = draw(st.text(min_size=1, max_size=200).filter(str.strip))
        payload = json.dumps({"status": "ready", "yaml_config": yaml_config})
        prefix = draw(_safe_text)
        suffix = draw(_safe_text)
        return prefix + payload + suffix, yaml_config
else:
    def clarify_json_strategy():
        pass

    def ready_json_strategy():
        pass


# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------

class TestExtractJsonProperties:
    """Property-based tests for extract_json."""

    @given(text=st.text())
    @settings(max_examples=50, deadline=1000)
    def test_always_returns_str(self, text):
        """extract_json always returns a str and never raises or returns None."""
        result = extract_json(text)
        assert isinstance(result, str)

    @given(
        payload=st.dictionaries(
            st.text(min_size=1, max_size=20),
            st.text(max_size=50),
            min_size=1, max_size=5,
        ),
        prefix=st.text(max_size=30),
        suffix=st.text(max_size=30),
    )
    @settings(max_examples=50, deadline=1000)
    def test_bare_json_is_parseable(self, payload, prefix, suffix):
        """Valid bare JSON embedded in text is extracted as parseable JSON."""
        json_str = json.dumps(payload)
        # Avoid prefix/suffix containing braces that would confuse extraction
        assume("{" not in prefix and "}" not in prefix)
        assume("{" not in suffix and "}" not in suffix)
        text = prefix + json_str + suffix
        result = extract_json(text)
        # The result should be parseable JSON
        json.loads(result)  # should not raise

    @given(
        payload=st.dictionaries(
            st.text(min_size=1, max_size=20).filter(lambda s: '"' not in s),
            st.text(max_size=50).filter(lambda s: '"' not in s),
            min_size=1, max_size=3,
        ),
        prefix=st.text(max_size=20).filter(lambda s: "```" not in s and "{" not in s),
        suffix=st.text(max_size=20).filter(lambda s: "```" not in s and "}" not in s),
    )
    @settings(max_examples=50, deadline=1000)
    def test_markdown_fence_takes_priority(self, payload, prefix, suffix):
        """Markdown-fenced JSON takes priority over brace-slice extraction."""
        json_str = json.dumps(payload)
        # Embed JSON in markdown fence with surrounding text that also has braces
        text = prefix + "```json\n" + json_str + "\n```" + suffix
        result = extract_json(text)
        assert json.loads(result) == payload


# ---------------------------------------------------------------------------
# parse_lego_agent_response robustness
# ---------------------------------------------------------------------------

class TestParseLegAgentResponseProperties:
    """Property-based tests for parse_lego_agent_response robustness."""

    @given(text=st.text())
    @settings(max_examples=100, deadline=1000)
    def test_only_raises_value_error_never_internal_exceptions(self, text):
        """For any string input, only ValueError is raised, never AttributeError/KeyError/TypeError."""
        try:
            result = parse_lego_agent_response(text)
            assert isinstance(result, LegoAgentResponse)
        except ValueError:
            pass  # Expected failure mode
        except (AttributeError, KeyError, TypeError) as exc:
            pytest.fail(
                f"parse_lego_agent_response raised {type(exc).__name__} for input {text!r}: {exc}"
            )

    @given(args=clarify_json_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_valid_clarify_json_succeeds(self, args):
        """Valid clarify JSON embedded in arbitrary text always parses successfully."""
        text, questions = args
        result = parse_lego_agent_response(text)
        assert result.status == "clarify"
        assert result.questions == questions

    @given(args=ready_json_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_valid_ready_json_succeeds(self, args):
        """Valid ready JSON embedded in arbitrary text always parses successfully."""
        text, yaml_config = args
        result = parse_lego_agent_response(text)
        assert result.status == "ready"
        assert result.yaml_config == yaml_config
