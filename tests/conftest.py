import pytest
from unittest.mock import MagicMock
from app_operator.logger import logger
from app_operator.filesystem import InMemoryFilesystem
from tests.fixtures.agents import (
    StubAgent,
    ErrorAgent,
    TimeoutAgent,
    TrackingAgent,
    ConfigurableAgent,
)


@pytest.fixture
def capture_logs():
    """Fixture to capture loguru logs."""
    logs = []
    logger.remove()
    logger.add(lambda msg: logs.append(msg))
    yield logs
    # Restore default behavior (optional, but good practice if tests run sequentially)
    logger.remove()


@pytest.fixture
def test_filesystem():
    """In-memory filesystem for testing without disk I/O."""
    return InMemoryFilesystem()


@pytest.fixture
def stub_agent():
    """Basic stub agent that returns simple responses."""
    return StubAgent()


@pytest.fixture
def error_agent():
    """Agent that always raises errors."""
    return ErrorAgent()


@pytest.fixture
def timeout_agent():
    """Agent that simulates timeouts."""
    return TimeoutAgent()


@pytest.fixture
def tracking_agent():
    """Agent that tracks all calls for verification."""
    return TrackingAgent()


@pytest.fixture
def configurable_agent():
    """Agent with configurable responses for different scenarios."""
    return ConfigurableAgent()


@pytest.fixture
def mock_subprocess(monkeypatch):
    """Mock subprocess operations for agent CLI tests."""
    mock_popen = MagicMock()
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate.return_value = (b"", b"")
    mock_process.poll.return_value = 0
    mock_popen.return_value = mock_process

    mock_which = MagicMock()
    mock_which.return_value = "/usr/bin/agent"

    monkeypatch.setattr("subprocess.Popen", mock_popen)
    monkeypatch.setattr("shutil.which", mock_which)

    return mock_popen, mock_which


@pytest.fixture
def repo_with_scripts(tmp_path):
    """Repository with pre-generated deployment scripts.

    Creates a temporary repository with working deploy.sh and
    health_check.sh scripts in the .sds directory.

    Returns:
        Path: Path to the repository root.
    """
    repo = tmp_path / "test_repo"
    repo.mkdir()

    # Create .sds directory
    sds_dir = repo / ".sds"
    sds_dir.mkdir()

    # Create working deploy script
    deploy_script = sds_dir / "deploy.sh"
    deploy_script.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        "echo 'Starting deployment...'\n"
        "echo 'Deployment successful'\n"
        "exit 0\n"
    )
    deploy_script.chmod(0o755)

    # Create working health check script
    health_script = sds_dir / "health_check.sh"
    health_script.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        "echo 'Running health checks...'\n"
        "echo 'All checks passed'\n"
        "exit 0\n"
    )
    health_script.chmod(0o755)

    return repo
