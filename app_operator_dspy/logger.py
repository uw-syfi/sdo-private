"""Logging for app_operator_dspy with agent-specific prefixes."""

import sys

from loguru import logger


def _formatter(record):
    agent = record["extra"].get("agent", "")
    prefix = f"[{agent}] " if agent else ""
    return f"<green>{record['time']:HH:mm:ss}</green> | {prefix}<level>{{message}}</level>\n"


def setup_logger() -> None:
    """Configure loguru for app_operator_dspy."""
    logger.remove()
    logger.add(sys.stderr, format=_formatter, level="INFO")


def get_logger(agent: str):
    """Return a logger bound with the given agent prefix."""
    return logger.bind(agent=agent)
