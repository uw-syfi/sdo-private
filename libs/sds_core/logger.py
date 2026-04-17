"""Shared loguru logger with the project's console formatter.

This is the Layer 0 logger used by every app under the repo (`app_operator`,
`lego_agent`, `sregym_agents`, and the `libs/*` packages).  The loguru logger
is a process-wide singleton; configuring it here means every importer gets the
same formatting without having to call `setup_logger()` themselves.
"""

import sys

from loguru import logger


def formatter(record):
    """Custom formatter that changes format based on presence of agent_prefix or stderr."""
    node = record["extra"].get("node")
    node_prefix = f"<cyan>[{node}]</cyan> " if node else ""
    if "agent_prefix" in record["extra"]:
        # Check if this is a stderr line
        if record["extra"].get("stderr", False):
            return "{extra[agent_prefix]} <red>{message}</red>\n"
        return "{extra[agent_prefix]} {message}\n"
    base = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    # Check if this is a stderr line without agent_prefix
    if record["extra"].get("stderr", False):
        return f"{base}{node_prefix}<red>{{message}}</red>\n"
    return f"{base}{node_prefix}<level>{{message}}</level>\n"


def setup_logger() -> None:
    """Configure the shared loguru logger to write to stderr with our format."""
    logger.remove()  # Remove default handler

    # Add a handler that writes to stderr with a clean format
    logger.add(
        sys.stderr,
        format=formatter,
        level="INFO",
    )


# Initialize logger immediately so any importer sees configured output.
setup_logger()

__all__ = ["formatter", "logger", "setup_logger"]
