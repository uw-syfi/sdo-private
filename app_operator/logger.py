import sys
from typing import TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from app_operator.ui.base import OperatorUI


def formatter(record):
    """Custom formatter that changes format based on presence of agent_prefix or stderr."""
    if "agent_prefix" in record["extra"]:
        # Check if this is a stderr line
        if record["extra"].get("stderr", False):
            return "{extra[agent_prefix]} <red>{message}</red>\n"
        return "{extra[agent_prefix]} {message}\n"
    # Check if this is a stderr line without agent_prefix
    if record["extra"].get("stderr", False):
        return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <red>{message}</red>\n"
    return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>\n"


def setup_logger():
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

    def sink(message):
        record = message.record
        text = record["message"]
        level = record["level"].name.lower()
        ui.log(text, level)

    logger.add(sink, format="{message}", level="INFO")


# Initialize logger immediately
setup_logger()
