"""Thin LiteLLM wrapper with automatic token tracking and trajectory recording."""

import os
from typing import Any

from agentshim.trajectory import TrajectoryRecorderProtocol

from ._retry import litellm_call_with_retry


class LiteLLMClient:
    """LiteLLM completion wrapper with token tracking and recorder integration."""

    def __init__(
        self,
        model: str,
        location: str | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
    ):
        self.model = model
        self.location = location
        self.recorder = recorder
        self._token_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def complete(self, messages: list[dict[str, Any]], label: str = "llm call") -> str:
        """Call LiteLLM with full caller-managed *messages* history."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "cache": {"no-cache": True},
        }
        loc = self.location or os.environ.get("VERTEX_LOCATION")
        if loc:
            kwargs["vertex_location"] = loc

        result = litellm_call_with_retry(kwargs, label=label, token_acc=self._token_usage)

        if self.recorder and hasattr(self.recorder, "record_token_usage"):
            self.recorder.record_token_usage(self._token_usage.copy())  # type: ignore[reportArgumentType]

        return result
