"""Fix history summarization for the deployment agent."""

import dspy

from app_operator_dspy.signatures import ConsolidateFixSummary


class Summarizer:
    """Tracks fix attempt summaries and consolidates them to avoid repeating failed approaches.

    Maintains history as internal state. On first fix: stores the summary as-is (no LLM call).
    On subsequent fixes: calls ConsolidateFixSummary to merge new attempts into grouped history.
    """

    def __init__(self):
        self.consolidate = dspy.ChainOfThought(ConsolidateFixSummary)
        self._history: str = ""

    @property
    def history(self) -> str:
        """Current fix history for passing to the repair agent."""
        return self._history

    def append(self, attempt: int, fix_summary: str) -> None:
        """Append a new fix summary; consolidate when prior history exists."""
        summary = f"Attempt {attempt}: {fix_summary}"
        if not self._history:
            self._history = summary
            return
        result = self.consolidate(
            existing_summary=self._history,
            new_attempts=summary,
        )
        self._history = result.consolidated_summary

    def reset(self) -> None:
        """Clear history at the start of a new deployment run."""
        self._history = ""
