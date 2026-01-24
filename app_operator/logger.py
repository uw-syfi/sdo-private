import sys
from loguru import logger


def formatter(record):
    """Custom formatter that changes format based on presence of agent_prefix."""
    if "agent_prefix" in record["extra"]:
        return "{extra[agent_prefix]} {message}\n"
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
