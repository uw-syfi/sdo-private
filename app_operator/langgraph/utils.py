from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

if TYPE_CHECKING:
    from app_operator.types import TokenUsage

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig

from app_operator.constants import LANGGRAPH_AGENT_RECURSION_LIMIT
from app_operator.langgraph._trajectory_handler import LangGraphTrajectoryHandler
from app_operator.langgraph.message_utils import extract_text
from app_operator.langgraph.state import OperatorState
from app_operator.logger import logger
from app_operator.script_runner import run_script as run_script
from app_operator.script_runner import write_log_file as write_log_file
from app_operator.trajectory import TrajectoryRecorderProtocol
from app_operator.ui_protocol import NullOperatorUI, OperatorUI

_DISPLAY_HEAD = 500
_DISPLAY_TAIL = 300


def _truncate_for_display(text: str) -> str:
    total = _DISPLAY_HEAD + _DISPLAY_TAIL
    if len(text) <= total:
        return text
    omitted = len(text) - total
    return f"{text[:_DISPLAY_HEAD]}\n... ({omitted} chars omitted) ...\n{text[-_DISPLAY_TAIL:]}"


T = TypeVar("T")


@dataclass
class AgentResult(Generic[T]):
    text: str
    messages: list[BaseMessage]
    structured: T | None


def _extract_token_usage(message: BaseMessage) -> dict[str, int]:
    if not isinstance(message, AIMessage):
        return {}

    usage = {"input": 0, "output": 0, "total": 0}

    # Prefer the standardized usage_metadata on the message (works across all providers)
    um = getattr(message, "usage_metadata", None)
    if um:
        usage["input"] = um.get("input_tokens", 0)
        usage["output"] = um.get("output_tokens", 0)
        usage["total"] = um.get("total_tokens", 0) or (usage["input"] + usage["output"])
        return usage

    # Fall back to provider-specific response_metadata
    metadata = message.response_metadata or {}

    if "token_usage" in metadata:  # OpenAI
        tu = metadata["token_usage"]
        usage["input"] = tu.get("prompt_tokens", 0)
        usage["output"] = tu.get("completion_tokens", 0)
        usage["total"] = tu.get("total_tokens", 0)
    elif "usage" in metadata:  # Anthropic
        tu = metadata["usage"]
        usage["input"] = tu.get("input_tokens", 0)
        usage["output"] = tu.get("output_tokens", 0)
        usage["total"] = usage["input"] + usage["output"]

    return usage


def _record_session_usage(
    state: OperatorState,
    agent_name: str,
    usage: dict[str, int],
    recorder: TrajectoryRecorderProtocol | None = None,
) -> None:
    sessions = state.get("agent_token_usage")
    if sessions is None:
        sessions = []
        state["agent_token_usage"] = sessions
    sessions.append(
        {
            "agent": agent_name,
            "input": usage.get("input", 0),
            "output": usage.get("output", 0),
            "total": usage.get("total", 0),
            "own_input": usage.get("input", 0),
            "own_output": usage.get("output", 0),
            "own_total": usage.get("total", 0),
            "subagents": [],
        }
    )
    if recorder is not None:
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for s in sessions:
            totals["prompt_tokens"] += s.get("input", 0)
            totals["completion_tokens"] += s.get("output", 0)
            totals["total_tokens"] += s.get("total", 0)
        recorder.record_token_usage(cast("TokenUsage", totals))
        traj = getattr(recorder, "trajectory", None)
        if traj is not None:
            traj["metadata"]["agent_token_usage"] = sessions


def _last_assistant_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content:
            return extract_text(content)
    return ""


def _invoke_agent_core(
    state: OperatorState,
    agent: Any,
    system_prompt: str,
    user_prompt: str,
    agent_name: str,
    context_limit: int,
    recorder: TrajectoryRecorderProtocol | None,
    ui: OperatorUI,
    logger,
    prior_messages: list[BaseMessage] | None = None,
) -> tuple[str, list[BaseMessage], Any | None]:
    """Shared streaming loop for invoke_agent and invoke_agent_structured."""
    handler = LangGraphTrajectoryHandler(recorder)

    if prior_messages is not None:
        messages: list[BaseMessage] = list(prior_messages) + [HumanMessage(content=user_prompt)]
    elif system_prompt:
        messages: list[BaseMessage] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    else:
        messages: list[BaseMessage] = [HumanMessage(content=user_prompt)]

    for msg in messages:
        handler.record_message(msg)

    response_messages = list(messages)
    total_usage = {"input": 0, "output": 0, "total": 0}
    structured_response = None

    logger.info("=" * 50)
    logger.info(f"Executing {agent_name}...")
    logger.info("=" * 50)

    agent_config = RunnableConfig(recursion_limit=LANGGRAPH_AGENT_RECURSION_LIMIT)
    for chunk in agent.stream({"messages": messages}, stream_mode="updates", config=agent_config):
        for _node_name, updates in chunk.items():
            if "structured_response" in updates:
                structured_response = updates["structured_response"]

            new_messages = updates.get("messages", [])
            if not new_messages:
                continue

            response_messages.extend(new_messages)

            for msg in new_messages:
                handler.record_message(msg)

                if isinstance(msg, AIMessage):
                    usage = _extract_token_usage(msg)
                    total_usage["input"] += usage.get("input", 0)
                    total_usage["output"] += usage.get("output", 0)
                    total_usage["total"] += usage.get("total", 0)

                    if msg.tool_calls:
                        for tool_call in msg.tool_calls:
                            logger.info(f"[Tool Call] {tool_call['name']}({tool_call['args']})")
                            ui.on_tool_call(tool_call["name"], str(tool_call["args"]))

                    content_text = extract_text(msg.content)
                    if content_text:
                        logger.info(content_text)

                    if usage.get("total", 0) > 0:
                        pct = round((usage["total"] / context_limit) * 100, 1)
                        logger.info(f"Token Usage: {pct}% ({usage['total']}/{context_limit})")

                elif isinstance(msg, ToolMessage):
                    content = _truncate_for_display(extract_text(msg.content))
                    logger.info(f"[Tool Result] {content}")

    logger.info("=" * 50)

    _record_session_usage(state, agent_name, total_usage, recorder)

    assistant_text = _last_assistant_text(response_messages)
    return assistant_text, response_messages, structured_response


def invoke_agent(
    state: OperatorState,
    agent: Any,
    system_prompt: str,
    user_prompt: str,
    agent_name: str = "Agent",
    context_limit: int = 128000,
    recorder: TrajectoryRecorderProtocol | None = None,
    ui: OperatorUI | None = None,
    logger=logger,
    prior_messages: list[BaseMessage] | None = None,
) -> AgentResult:
    """Invoke a LangGraph agent and stream its output.

    The `logger` parameter accepts a loguru-bound logger so that callers (e.g.
    individual langgraph nodes) can propagate their node-name binding into all
    log lines emitted here.  Without this, every log line from this shared
    utility would use the module-level unbound logger, losing the [node] prefix
    that the formatter adds when the 'node' extra is present.

    If `prior_messages` is provided, the new user_prompt is appended to that
    history, continuing the conversation rather than starting fresh.

    Returns an AgentResult with .text, .messages, and .structured fields.
    .structured is populated when the agent was created with response_format=.
    """
    text, messages, structured = _invoke_agent_core(
        state,
        agent,
        system_prompt,
        user_prompt,
        agent_name,
        context_limit,
        recorder,
        ui or NullOperatorUI(),
        logger,
        prior_messages=prior_messages,
    )
    return AgentResult(text=text, messages=messages, structured=structured)
