"""DeepAgents Judge agent factory."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model

from sregym_agents.crucible_deepagents.tools import (
    JudgeDeps,
    TrajectoryCallbackHandler,
    make_reveal_agent_hypothesis_tool,
    make_submit_independent_findings_tool,
    make_submit_verdict_tool,
)
from sregym_agents.crucible_simple.shell_backend import TimeoutShellBackend

logger = logging.getLogger(__name__)

MAX_SUBMIT_REMINDERS = 3


async def run_judge_agent(
    model_id: str,
    stage: str,
    deps: JudgeDeps,
    system_prompt: str,
    user_prompt: str,
    trajectory_path: Path | None = None,
    run_ctx: dict[str, Any] | None = None,
) -> tuple[str, dict]:
    """Create and run a DeepAgents Judge agent. Returns (output, usage_dict)."""
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}

    # Build custom tools
    custom_tools = [
        make_submit_independent_findings_tool(deps),
        make_reveal_agent_hypothesis_tool(deps),
        make_submit_verdict_tool(deps),
    ]

    # Create the DeepAgents agent (no subagents for judge)
    agent = create_deep_agent(
        model=init_chat_model(model_id, temperature=0),
        tools=custom_tools,
        backend=TimeoutShellBackend(virtual_mode=True, inherit_env=True),
        system_prompt=system_prompt,
    )

    # Set up trajectory callback
    callbacks = []
    trajectory_handler: TrajectoryCallbackHandler | None = None
    if trajectory_path is not None:
        trajectory_handler = TrajectoryCallbackHandler(
            trajectory_path,
            agent_name=f"judge-{stage}",
            run_ctx=run_ctx,
        )
        callbacks.append(trajectory_handler)

    config: dict[str, Any] = {}
    if callbacks:
        config["callbacks"] = callbacks

    # Run the agent with submit reminder loop
    messages = [{"role": "user", "content": user_prompt}]
    reminder_count = 0
    output = ""

    while True:
        result = await agent.ainvoke({"messages": messages}, config=config)

        # Extract final message content
        result_messages = result.get("messages", [])
        if result_messages:
            last_msg = result_messages[-1]
            if hasattr(last_msg, "content"):
                output = last_msg.content
            elif isinstance(last_msg, dict):
                output = last_msg.get("content", "")

        if deps.state.submitted:
            break

        if reminder_count >= MAX_SUBMIT_REMINDERS:
            logger.warning("Max judge submit reminders reached — giving up.")
            break

        reminder_count += 1
        logger.warning(f"Judge stopped without submitting (reminder {reminder_count}/{MAX_SUBMIT_REMINDERS}).")
        # Continue conversation with reminder
        messages = result_messages
        messages.append(
            {
                "role": "user",
                "content": (
                    "You have not submitted your verdict yet. "
                    "Please call submit_verdict with your final verdict before finishing."
                ),
            }
        )

    # Extract usage from trajectory handler
    if trajectory_handler is not None:
        trajectory_handler.flush()
        handler_usage = trajectory_handler.usage
        usage["input_tokens"] += handler_usage.get("input_tokens", 0)
        usage["output_tokens"] += handler_usage.get("output_tokens", 0)

    return output, usage
