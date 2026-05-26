"""Tests for the canonical message schema (messages.py).

Verifies that TaggedMsg serializes correctly and that parse_server_msg
reconstructs the right variant from a flat dict — the two halves of the
multiplexed stream contract between runtime.py and server.py.
"""

import json

import pytest

from lego_agent.backend.messages import (
    LogMsg,
    TaggedMsg,
    ThinkingMsg,
    ToolEndMsg,
    ToolStartMsg,
    parse_server_msg,
)

# ── TaggedMsg.to_json_line ────────────────────────────────────────────────────


def test_tagged_msg_flat_json():
    """to_json_line produces a flat JSON object with agent_id at the top level."""
    msg = TaggedMsg(agent_id="worker_0", inner=ThinkingMsg(type="thinking", text="hello"))
    line = msg.to_json_line()
    data = json.loads(line)

    assert data == {"agent_id": "worker_0", "type": "thinking", "text": "hello"}


def test_tagged_msg_tool_start():
    msg = TaggedMsg(
        agent_id="worker_1",
        inner=ToolStartMsg(type="tool_start", name="bash", input="ls -la"),
    )
    data = json.loads(msg.to_json_line())

    assert data["agent_id"] == "worker_1"
    assert data["type"] == "tool_start"
    assert data["name"] == "bash"
    assert data["input"] == "ls -la"


def test_tagged_msg_log_levels():
    for level in ("info", "error", "success"):
        msg = TaggedMsg(
            agent_id="w",
            inner=LogMsg(type="log", message="test", level=level),  # type: ignore[arg-type]
        )
        data = json.loads(msg.to_json_line())
        assert data["level"] == level


# ── parse_server_msg ──────────────────────────────────────────────────────────


def test_parse_thinking_msg():
    result = parse_server_msg({"type": "thinking", "text": "reasoning..."})
    assert isinstance(result, ThinkingMsg)
    assert result.text == "reasoning..."


def test_parse_tool_end_msg():
    result = parse_server_msg({"type": "tool_end", "name": "bash", "output": "ok", "status": "success"})
    assert isinstance(result, ToolEndMsg)
    assert result.status == "success"


def test_parse_ignores_agent_id():
    """agent_id in the dict must not cause a TypeError — it's silently dropped."""
    result = parse_server_msg({"agent_id": "worker_0", "type": "thinking", "text": "hi"})
    assert isinstance(result, ThinkingMsg)
    assert result.text == "hi"


def test_parse_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown server message type"):
        parse_server_msg({"type": "not_a_real_type"})


def test_round_trip_via_tagged_msg():
    """TaggedMsg → JSON line → parse_server_msg reconstructs the inner message."""
    original = LogMsg(type="log", message="hello world", level="info")
    tagged = TaggedMsg(agent_id="worker_2", inner=original)

    data = json.loads(tagged.to_json_line())
    reconstructed = parse_server_msg(data)

    assert isinstance(reconstructed, LogMsg)
    assert reconstructed.message == "hello world"
    assert reconstructed.level == "info"
