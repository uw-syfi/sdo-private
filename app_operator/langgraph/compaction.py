from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from app_operator.logger import logger as _logger

logger = _logger.bind(node="compaction")

_TAIL_MESSAGES = 6  # last N messages always kept verbatim
_SUMMARY_SYSTEM = (
    "You are a summarizer. Summarize the following agent tool interactions "
    "concisely: what was found, what was tried, and what outcomes occurred. "
    "Be brief but retain key facts needed to continue the task."
)


def _split_messages(messages: list[BaseMessage], tail: int) -> tuple[list, list, list]:
    """Split into (head, middle, tail) where head = system + first human."""
    head: list[BaseMessage] = []
    rest = list(messages)

    # Keep leading system messages
    while rest and isinstance(rest[0], SystemMessage):
        head.append(rest.pop(0))

    # Keep the first human message (the original task prompt)
    if rest and isinstance(rest[0], HumanMessage):
        head.append(rest.pop(0))

    if len(rest) <= tail:
        return head, [], rest

    middle = rest[:-tail]
    tail_msgs = rest[-tail:]
    return head, middle, tail_msgs


def _format_for_summary(messages: list[BaseMessage]) -> str:
    parts = []
    for msg in messages:
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                calls = ", ".join(tc["name"] for tc in msg.tool_calls)
                parts.append(f"[Agent called tools: {calls}]")
            if isinstance(msg.content, str) and msg.content.strip():
                parts.append(f"[Agent said: {msg.content.strip()}]")
        elif isinstance(msg, ToolMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            parts.append(f"[Tool result ({msg.name}): {content[:500]}{'...' if len(content) > 500 else ''}]")
        elif isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            parts.append(f"[Human: {content[:200]}{'...' if len(content) > 200 else ''}]")
    return "\n".join(parts)


def make_compaction_hook(llm, context_limit: int, threshold: float = 0.75):
    """Return a pre_model_hook for create_react_agent that summarizes message
    history with the LLM when messages exceed `threshold * context_limit` tokens."""
    max_tokens = int(context_limit * threshold)

    def pre_model_hook(state: dict) -> dict:
        messages = state["messages"]
        approx = count_tokens_approximately(messages)
        if approx <= max_tokens:
            return {"llm_input_messages": messages}

        logger.info(f"Context compaction triggered: ~{approx} tokens > threshold {max_tokens}")

        head, middle, tail_msgs = _split_messages(messages, tail=_TAIL_MESSAGES)

        if not middle:
            # Nothing to summarize — just return as-is and accept the risk
            logger.warning("Cannot compact: no middle messages to summarize")
            return {"llm_input_messages": messages}

        formatted = _format_for_summary(middle)
        try:
            summary_response = llm.invoke(
                [
                    SystemMessage(content=_SUMMARY_SYSTEM),
                    HumanMessage(content=formatted),
                ]
            )
            summary_text = (
                summary_response.content if isinstance(summary_response.content, str) else str(summary_response.content)
            )
        except Exception as e:
            logger.warning(f"Summarization failed ({e}); falling back to dropping middle messages")
            summary_text = f"[{len(middle)} earlier messages omitted due to context length]"

        summary_msg = HumanMessage(content=f"Summary of previous work:\n{summary_text}")
        compacted = head + [summary_msg] + tail_msgs

        logger.info(
            f"Compacted: {len(messages)} -> {len(compacted)} messages, ~{count_tokens_approximately(compacted)} tokens"
        )
        return {"llm_input_messages": compacted}

    return pre_model_hook
