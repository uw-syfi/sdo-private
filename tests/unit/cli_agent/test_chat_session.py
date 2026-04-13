"""Tests for the interactive hybrid terminal chat session."""

import unittest.mock as mock

from app_operator.cli_agent.chat_session import HybridTerminalChatSession


def _make_litellm_response(content: str, prompt_tokens=10, completion_tokens=5):
    resp = mock.MagicMock()
    resp.choices = [mock.MagicMock()]
    resp.choices[0].message.content = content
    usage = mock.MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = prompt_tokens + completion_tokens
    resp.usage = usage
    return resp


def test_reply_uses_chat_intro_and_current_user_message(tmp_path):
    (tmp_path / "README.md").write_text("# Demo repo\n")
    session = HybridTerminalChatSession(
        repo_path=str(tmp_path),
        model="test-model",
        intro_prompt="You are a repo guide.",
    )
    captured_prompts = []

    def fake_completion(**kwargs):
        captured_prompts.append(kwargs["messages"])
        return _make_litellm_response("ACTION: final_answer\nANSWER: hello")

    with mock.patch("litellm.completion", side_effect=fake_completion):
        answer = session.reply("What is in this repository?")

    assert answer == "hello"
    user_prompt = captured_prompts[0][1]["content"]
    assert "You are a repo guide." in user_prompt
    assert "Current user message:\nWhat is in this repository?" in user_prompt
    assert "interactive terminal chat mode" in user_prompt


def test_reply_includes_recent_history_on_second_turn(tmp_path):
    session = HybridTerminalChatSession(repo_path=str(tmp_path), model="test-model")
    captured_prompts = []

    def fake_completion(**kwargs):
        captured_prompts.append(kwargs["messages"])
        answer = "first" if len(captured_prompts) == 1 else "second"
        return _make_litellm_response(f"ACTION: final_answer\nANSWER: {answer}")

    with mock.patch("litellm.completion", side_effect=fake_completion):
        session.reply("First question?")
        session.reply("Second question?")

    second_prompt = captured_prompts[1][1]["content"]
    assert "User: First question?" in second_prompt
    assert "Assistant: first" in second_prompt
    assert "Current user message:\nSecond question?" in second_prompt
