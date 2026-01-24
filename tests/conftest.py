import pytest
from app_operator.logger import logger


@pytest.fixture
def capture_logs():
    """Fixture to capture loguru logs."""
    logs = []
    logger.remove()
    logger.add(lambda msg: logs.append(msg))
    yield logs
    # Restore default behavior (optional, but good practice if tests run sequentially)
    logger.remove()
