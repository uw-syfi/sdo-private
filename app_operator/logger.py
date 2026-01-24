import sys
from loguru import logger


def setup_logger():
    """Configure the logger for the application."""
    logger.remove()  # Remove default handler

    # Add a handler that writes to stderr with a clean format
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO",
    )


# Initialize logger immediately
setup_logger()
