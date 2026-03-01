"""Progress summarization for long-running deployment processes."""

import re
import time
from typing import Optional, Callable

from app_operator.logger import logger
from app_operator.prompts import get_loader


class ProgressSummarizer:
    """Periodically summarizes progress of long-running processes using an agent.

    This class handles:
    - Tracking elapsed time since process start
    - Periodic summarization at configurable intervals
    - Initial delay before first summary
    - Agent invocation for generating summaries
    """

    def __init__(
        self,
        agent_generate_fn: Callable[[str, bool, int], str],
        initial_delay: float = 15.0,
        summary_interval: float = 30.0,
        time_func: Optional[Callable[[], float]] = None,
        recorder=None,
    ):
        """Initialize the progress summarizer.

        Args:
            agent_generate_fn: Function to call for generating summaries.
                Should accept (prompt, silent, timeout) and return response string.
            initial_delay: Seconds to wait before first summary (default: 15).
            summary_interval: Seconds between summaries (default: 30).
            time_func: Optional function to get current time (default: time.time).
            recorder: Optional trajectory recorder.
        """
        self.agent_generate_fn = agent_generate_fn
        self.initial_delay = initial_delay
        self.summary_interval = summary_interval
        self.time_func = time_func if time_func is not None else time.time
        self.recorder = recorder

        self.start_time: Optional[float] = None
        self.last_summary_time: Optional[float] = None

    def start(self, start_time: Optional[float] = None):
        """Start the summarization timer.

        Args:
            start_time: Optional start time. If None, uses time_func().
        """
        if start_time is None:
            start_time = self.time_func()
        self.start_time = start_time
        self.last_summary_time = start_time

    def should_summarize(self) -> bool:
        """Check if it's time to generate a summary.

        Returns:
            bool: True if a summary should be generated.
        """
        if self.start_time is None or self.last_summary_time is None:
            logger.debug("ProgressSummarizer: should_summarize=False (not started)")
            return False

        current_time = self.time_func()
        elapsed = current_time - self.start_time
        time_since_last = current_time - self.last_summary_time

        should = elapsed > self.initial_delay and time_since_last >= self.summary_interval

        if should:
            logger.debug(
                f"ProgressSummarizer: should_summarize=True "
                f"(elapsed={elapsed:.1f}s > {self.initial_delay}s, "
                f"time_since_last={time_since_last:.1f}s >= {self.summary_interval}s)"
            )

        return should

    def summarize(self, output_snippet: str):
        """Generate and log a progress summary.

        Args:
            output_snippet: Recent output to summarize.
        """
        if not output_snippet.strip():
            logger.debug("ProgressSummarizer: summarize skipped (empty output)")
            return

        if self.start_time is None:
            logger.debug("ProgressSummarizer: summarize skipped (not started)")
            return

        elapsed_time = self.time_func() - self.start_time

        logger.debug(
            f"ProgressSummarizer: Generating summary at {elapsed_time:.1f}s "
            f"(output length: {len(output_snippet)} chars)"
        )

        try:
            prompt = get_loader().render(
                "deployer/summarize.jinja2",
                output_snippet=output_snippet,
                recorder=self.recorder,
            )

            logger.debug(f"ProgressSummarizer: Calling agent with prompt ({len(prompt)} chars)")

            # Use silent=True to avoid printing the agent's internal thought process
            response = self.agent_generate_fn(prompt, True, 30)

            logger.debug(f"ProgressSummarizer: Got response ({len(response)} chars)")

            summary = self._extract_summary(response)
            if summary:
                logger.info(f"[{elapsed_time:.1f}s] ➜ {summary}")
            else:
                logger.warning("ProgressSummarizer: Failed to extract summary from response")

            # Update last summary time
            self.last_summary_time = self.time_func()

        except Exception as e:
            # If summarization fails, log but don't interrupt the flow
            logger.warning(f"ProgressSummarizer: Failed to generate summary: {e}", exc_info=True)
            pass

    def _extract_summary(self, response: str) -> Optional[str]:
        """Extract the summary from the agent's response using XML markers.

        Args:
            response: The agent's response text.

        Returns:
            Optional[str]: Extracted summary text, or None if not found.
        """
        match = re.search(r"<output_msg>(.*?)</output_msg>", response, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None
