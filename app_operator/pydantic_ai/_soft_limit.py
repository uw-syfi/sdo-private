"""Soft step-limit callbacks for pydantic_ai agents.

When a run reaches the soft threshold (hard limit - 5), two things happen:
1. A "please wrap up" system prompt is appended to the message history.
2. All function tools are hidden so the model must produce a final response.
"""

from __future__ import annotations

from pydantic_ai import RunContext  # noqa: TC002
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart
from pydantic_ai.tools import ToolDefinition  # noqa: TC002

from app_operator.logger import logger
from app_operator.pydantic_ai._deps import OperatorDeps  # noqa: TC001

_WRAP_UP_PROMPT = (
    "You are approaching the step limit. Please provide your final response now without making any further tool calls."
)


def _soft_threshold(step_limit: int | None) -> int | None:
    if step_limit is None:
        return None
    return max(0, step_limit - 5)


def soft_limit_history_processor(ctx: RunContext[OperatorDeps], messages: list[ModelMessage]) -> list[ModelMessage]:
    threshold = _soft_threshold(ctx.deps.config.agent.step_limit)
    if threshold is None or ctx.run_step < threshold:
        return messages
    logger.warning(
        "Soft step limit reached (step {}/{}). Asking model to wrap up.",
        ctx.run_step,
        ctx.deps.config.agent.step_limit,
    )
    return list(messages) + [ModelRequest(parts=[SystemPromptPart(content=_WRAP_UP_PROMPT)])]


async def soft_limit_prepare_tools(
    ctx: RunContext[OperatorDeps], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition] | None:
    threshold = _soft_threshold(ctx.deps.config.agent.step_limit)
    if threshold is None or ctx.run_step < threshold:
        return tool_defs
    return []
