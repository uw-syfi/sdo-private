"""Tests for app_operator_dspy.summarizer."""

from unittest.mock import MagicMock

import dspy

from app_operator_dspy.summarizer import Summarizer


class TestSummarizer:
    def test_has_consolidate_module(self):
        """Summarizer wraps a dspy ChainOfThought for consolidation."""
        summarizer = Summarizer()
        assert isinstance(summarizer.consolidate, dspy.ChainOfThought)

    def test_first_fix_returns_summary_without_consolidation(self):
        """First fix has no prior history — consolidation is skipped."""
        summarizer = Summarizer()
        summarizer.consolidate = MagicMock()

        summarizer.append(1, "fixed port conflict")

        assert summarizer.history == "Attempt 1: fixed port conflict"
        summarizer.consolidate.assert_not_called()

    def test_second_fix_calls_consolidation(self):
        """Second fix has prior history — consolidation is called."""
        summarizer = Summarizer()
        summarizer.consolidate = MagicMock(
            return_value=dspy.Prediction(
                consolidated_summary="## Failure Pattern: port conflict\n* Attempt 1: fixed\n* Attempt 2: retried"
            )
        )
        summarizer.append(1, "fixed port conflict")

        summarizer.append(2, "retried with different port")

        assert "Attempt 2" in summarizer.history
        summarizer.consolidate.assert_called_once()
        assert summarizer.history == "## Failure Pattern: port conflict\n* Attempt 1: fixed\n* Attempt 2: retried"

    def test_reset_clears_history(self):
        """reset() clears history for next deployment run."""
        summarizer = Summarizer()
        summarizer.append(1, "fixed")

        summarizer.reset()

        assert summarizer.history == ""
