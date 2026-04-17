import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Message, Record

    from app_operator.core.ui_protocol import OperatorUI


def formatter(record: "Record") -> str:
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
    """Configure the logger for the application."""
    logger.remove()  # Remove default handler

    # Add a handler that writes to stderr with a clean format
    logger.add(
        sys.stderr,
        format=formatter,
        level="INFO",
    )


def attach_ui_sink(ui: "OperatorUI", replace: bool = False) -> None:
    """Attach the logger to the UI sink."""
    if replace:
        logger.remove()

    def sink(message: "Message") -> None:
        record = message.record
        text = record["message"]
        level = record["level"].name.lower()
        ui.log(text, level)

    logger.add(sink, format="{message}", level="INFO")


# Initialize logger immediately
setup_logger()
