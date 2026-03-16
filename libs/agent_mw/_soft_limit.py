"""Soft step-limit middleware for pydantic-ai agents.

When a run reaches the soft threshold (hard limit - 5), two things happen:
1. A "please wrap up" system prompt is appended to the message history.
2. All function tools are hidden so the model must produce a final response.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ModelRequest, SystemPromptPart

from libs.pydantic_agent._middleware import AgentMiddleware

if TYPE_CHECKING:
    from libs.pydantic_agent._base import BaseAgent

logger = logging.getLogger(__name__)

_WRAP_UP_PROMPT = (
    "You are approaching the step limit. Please provide your final response now without making any further tool calls."
)


def _soft_threshold(step_limit: int | None) -> int | None:
    if step_limit is None:
        return None
    return max(0, step_limit - 5)


class SoftLimitExtension(AgentMiddleware):
    """Soft step limiter: sets UsageLimits and wraps up the agent near the limit."""

    def __init__(self, step_limit: int | None) -> None:
        self._step_limit = step_limit

    def on_attach(self, agent: BaseAgent) -> None:
        super().on_attach(agent)
        if self._step_limit is not None:
            from pydantic_ai.usage import UsageLimits

            agent._usage_limits = UsageLimits(request_limit=self._step_limit)

    def before_model_req_edit_messages(self, ctx: Any, messages: list) -> list:
        threshold = _soft_threshold(self._step_limit)
        if threshold is None or ctx.run_step < threshold:
            return messages
        logger.warning("Soft step limit reached (step %d/%s).", ctx.run_step, self._step_limit)
        return list(messages) + [ModelRequest(parts=[SystemPromptPart(content=_WRAP_UP_PROMPT)])]

    async def before_model_req_edit_tools(self, ctx: Any, tool_defs: list) -> list | None:
        threshold = _soft_threshold(self._step_limit)
        if threshold is None or ctx.run_step < threshold:
            return tool_defs
        return []
