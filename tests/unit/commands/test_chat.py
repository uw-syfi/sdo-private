"""Unit tests for app_operator.commands.chat."""

import argparse
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app_operator.commands.chat import (
    DEFAULT_CHAT_INTRO_PROMPT,
    DEFAULT_INITIAL_CHAT_MESSAGE,
    add_arguments,
    run_command,
)
from app_operator.config import AgentConfig, Config, RuntimeConfig
from libs.model_config import ModelConfig


def _make_config() -> Config:
    return Config(
        agent=AgentConfig(backend="hybrid", model_config=ModelConfig.from_string("test-model")),
        runtime=RuntimeConfig(impl="cli_agent"),
    )


def test_add_arguments_parses_directory_and_prompt_flags():
    parser = argparse.ArgumentParser()
    add_arguments(parser)

    args = parser.parse_args(
        [
            "/repo",
            "--config",
            "custom.toml",
            "--intro-prompt",
            "You are a repo assistant.",
            "--initial-message",
            "Check the repo.",
        ]
    )

    assert args.directory == "/repo"
    assert args.config == "custom.toml"
    assert args.intro_prompt == "You are a repo assistant."
    assert args.initial_message == "Check the repo."


def test_run_command_starts_session_and_prints_initial_reply():
    args = argparse.Namespace(
        directory="/repo",
        config=None,
        intro_prompt=DEFAULT_CHAT_INTRO_PROMPT,
        initial_message=DEFAULT_INITIAL_CHAT_MESSAGE,
    )
    config = _make_config()
    session = MagicMock()
    session.reply.return_value = "Initial overview"
    chat_mod = SimpleNamespace(HybridTerminalChatSession=MagicMock(return_value=session))

    with (
        patch("app_operator.commands.chat.load_config", return_value=config),
        patch("builtins.input", side_effect=["quit"]),
        patch("builtins.print") as mock_print,
        patch("app_operator.commands.chat.importlib.import_module", return_value=chat_mod),
    ):
        rc = run_command(args)

    assert rc == 0
    chat_mod.HybridTerminalChatSession.assert_called_once()
    session.reply.assert_called_once_with(DEFAULT_INITIAL_CHAT_MESSAGE)
    assert any("Initial overview" in str(call.args[0]) for call in mock_print.call_args_list if call.args)


def test_run_command_handles_interactive_turns():
    args = argparse.Namespace(
        directory="/repo",
        config="/repo/sds.toml",
        intro_prompt="Custom intro",
        initial_message="Initial check",
    )
    config = _make_config()
    session = MagicMock()
    session.reply.side_effect = ["Initial overview", "Answer to question"]
    chat_mod = SimpleNamespace(HybridTerminalChatSession=MagicMock(return_value=session))

    with (
        patch("app_operator.commands.chat.load_config", return_value=config) as mock_load,
        patch("builtins.input", side_effect=["What is this repo?", "exit"]),
        patch("builtins.print") as mock_print,
        patch("app_operator.commands.chat.importlib.import_module", return_value=chat_mod),
    ):
        rc = run_command(args)

    assert rc == 0
    mock_load.assert_called_once_with("/repo", "/repo/sds.toml")
    assert session.reply.call_args_list[0].args == ("Initial check",)
    assert session.reply.call_args_list[1].args == ("What is this repo?",)
    assert any("Answer to question" in str(call.args[0]) for call in mock_print.call_args_list if call.args)


def test_run_command_returns_1_when_directory_empty():
    args = argparse.Namespace(
        directory="",
        config=None,
        intro_prompt=DEFAULT_CHAT_INTRO_PROMPT,
        initial_message=DEFAULT_INITIAL_CHAT_MESSAGE,
    )

    with patch("app_operator.commands.chat.logger") as mock_logger:
        rc = run_command(args)

    assert rc == 1
    mock_logger.error.assert_called_once()


def test_run_command_exits_cleanly_on_eof():
    args = argparse.Namespace(
        directory="/repo",
        config=None,
        intro_prompt=DEFAULT_CHAT_INTRO_PROMPT,
        initial_message=DEFAULT_INITIAL_CHAT_MESSAGE,
    )
    config = _make_config()
    session = MagicMock()
    session.reply.return_value = "Initial overview"
    chat_mod = SimpleNamespace(HybridTerminalChatSession=MagicMock(return_value=session))

    with (
        patch("app_operator.commands.chat.load_config", return_value=config),
        patch("builtins.input", side_effect=EOFError),
        patch("builtins.print"),
        patch("app_operator.commands.chat.importlib.import_module", return_value=chat_mod),
    ):
        rc = run_command(args)

    assert rc == 0
