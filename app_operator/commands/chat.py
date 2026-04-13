"""Interactive terminal chat command for the hybrid repo assistant."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from app_operator.config import load_config
from app_operator.logger import logger

if TYPE_CHECKING:
    import argparse

DEFAULT_CHAT_INTRO_PROMPT = (
    "You are a terminal codebase assistant for this repository. "
    "Inspect the local code before answering implementation questions, "
    "and keep answers concise unless the user asks for more detail."
)

DEFAULT_INITIAL_CHAT_MESSAGE = (
    "Check over this codebase and introduce yourself. "
    "Summarize the main components, likely purpose, and what you can help with."
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments specific to the interactive chat command."""
    parser.add_argument("directory", metavar="DIR", help="Repository path to inspect interactively")
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to configuration file (default: sds.toml in target dir)",
    )
    parser.add_argument(
        "--intro-prompt",
        default=DEFAULT_CHAT_INTRO_PROMPT,
        help="Hidden intro prompt/instruction for the chat agent",
    )
    parser.add_argument(
        "--initial-message",
        default=DEFAULT_INITIAL_CHAT_MESSAGE,
        help="Initial user message sent automatically when the chat starts",
    )


def run_command(args: argparse.Namespace) -> int:
    """Execute the interactive hybrid chat loop."""
    if not args.directory:
        logger.error("Error: Directory not specified for 'chat' command.")
        return 1

    try:
        config = load_config(args.directory, args.config)
        chat_mod = importlib.import_module("app_operator.cli_agent.chat_session")
        session = chat_mod.HybridTerminalChatSession(
            repo_path=args.directory,
            model=config.agent.model,
            location=config.agent.location,
            dspy_config=config.dspy,
            rlm_mode=config.rlm.mode,
            intro_prompt=args.intro_prompt,
        )

        print(f"Repo chat ready for {args.directory}")
        print("Type 'exit' or 'quit' to leave.\n")

        initial_reply = session.reply(args.initial_message)
        print(initial_reply)

        while True:
            try:
                user_input = input("\nchat> ").strip()
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print()
                return 0

            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit", ":q"}:
                return 0

            print(session.reply(user_input))

    except ValueError as e:
        logger.error(f"Error: {e}")
        return 1
    except (OSError, RuntimeError) as e:
        logger.error(f"✗ Unexpected error: {e}", exc_info=True)
        return 1
