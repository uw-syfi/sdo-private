"""Unit tests for app_operator/langgraph/compaction.py"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app_operator.langgraph.compaction import _TAIL_MESSAGES, make_compaction_hook


def _make_llm(summary_text: str = "Summary text") -> MagicMock:
    llm = MagicMock()
    response = MagicMock()
    response.content = summary_text
    llm.invoke.return_value = response
    return llm


def _tool_msg(content: str, name: str = "tool") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="id1", name=name)


def _ai_msg(content: str = "") -> AIMessage:
    return AIMessage(content=content)


def _make_long_messages(n: int) -> list:
    """Build a message list that is long enough to exceed threshold."""
    msgs = [
        SystemMessage(content="You are an agent."),
        HumanMessage(content="Deploy the app."),
    ]
    for i in range(n):
        msgs.append(_ai_msg(f"step {i} " * 50))
        msgs.append(_tool_msg(f"result {i} " * 50))
    return msgs


class TestUnderThreshold:
    def test_returns_messages_unchanged(self):
        llm = _make_llm()
        hook = make_compaction_hook(llm, context_limit=1_000_000, threshold=0.75)
        messages = [SystemMessage(content="sys"), HumanMessage(content="hi")]
        result = hook({"messages": messages})
        assert result["llm_input_messages"] is messages
        llm.invoke.assert_not_called()


class TestOverThreshold:
    def setup_method(self):
        # Use a tiny context limit so even a few messages exceed the threshold
        self.llm = _make_llm("Here is what happened so far.")
        self.hook = make_compaction_hook(self.llm, context_limit=10, threshold=0.75)
        self.messages = _make_long_messages(20)

    def test_llm_called_for_summary(self):
        self.hook({"messages": self.messages})
        self.llm.invoke.assert_called_once()

    def test_result_is_smaller(self):
        result = self.hook({"messages": self.messages})
        assert len(result["llm_input_messages"]) < len(self.messages)

    def test_system_message_preserved(self):
        result = self.hook({"messages": self.messages})
        compacted = result["llm_input_messages"]
        assert isinstance(compacted[0], SystemMessage)
        assert compacted[0].content == "You are an agent."

    def test_original_task_preserved(self):
        result = self.hook({"messages": self.messages})
        compacted = result["llm_input_messages"]
        # Second message should be the original HumanMessage task prompt
        assert isinstance(compacted[1], HumanMessage)
        assert compacted[1].content == "Deploy the app."

    def test_summary_injected(self):
        result = self.hook({"messages": self.messages})
        compacted = result["llm_input_messages"]
        summary_msgs = [
            m for m in compacted if isinstance(m, HumanMessage) and "Summary of previous work" in (m.content or "")
        ]
        assert len(summary_msgs) == 1
        assert "Here is what happened so far." in summary_msgs[0].content

    def test_tail_messages_preserved(self):
        result = self.hook({"messages": self.messages})
        compacted = result["llm_input_messages"]
        # The last _TAIL_MESSAGES messages from the original must appear verbatim at the end
        original_tail = self.messages[-_TAIL_MESSAGES:]
        compacted_tail = compacted[-_TAIL_MESSAGES:]
        for orig, comp in zip(original_tail, compacted_tail, strict=True):
            assert orig.content == comp.content


class TestSummarizationFailureFallback:
    def test_fallback_placeholder_on_llm_error(self):
        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("LLM unavailable")
        hook = make_compaction_hook(llm, context_limit=10, threshold=0.75)
        messages = _make_long_messages(20)
        result = hook({"messages": messages})
        compacted = result["llm_input_messages"]
        summary_msgs = [
            m for m in compacted if isinstance(m, HumanMessage) and "Summary of previous work" in (m.content or "")
        ]
        assert len(summary_msgs) == 1
        assert "omitted" in summary_msgs[0].content

    def test_no_crash_on_llm_error(self):
        llm = MagicMock()
        llm.invoke.side_effect = Exception("boom")
        hook = make_compaction_hook(llm, context_limit=10, threshold=0.75)
        messages = _make_long_messages(20)
        # Must not raise
        result = hook({"messages": messages})
        assert "llm_input_messages" in result


class TestEdgeNothingToSummarize:
    def test_returns_original_when_no_middle(self):
        """When there are only head + tail messages, nothing can be summarized."""
        llm = _make_llm()
        # Very small tail so that all non-head messages fit within it
        hook = make_compaction_hook(llm, context_limit=10, threshold=0.75)

        # Only system + first human + a few tail messages (fewer than _TAIL_MESSAGES)
        messages = [
            SystemMessage(content="sys"),
            HumanMessage(content="task"),
            _ai_msg("step 1"),
        ]
        result = hook({"messages": messages})
        # llm should NOT be called because there are no middle messages
        llm.invoke.assert_not_called()
        assert result["llm_input_messages"] is messages
