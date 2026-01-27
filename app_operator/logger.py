import sys
from loguru import logger


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


# Initialize logger immediately
setup_logger()
