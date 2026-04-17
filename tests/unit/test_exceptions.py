"""Tests for custom exception hierarchy."""

import pytest

from app_operator.exceptions import (
    AgentError,
    ConfigurationError,
    DeploymentError,
    FileSystemError,
    MonitoringError,
    ProcessError,
    SdsOperatorError,
)


def test_sds_operator_error_base():
    """Test base SdsOperatorError exception."""
    error = SdsOperatorError("test message")
    assert str(error) == "test message"
    assert isinstance(error, Exception)


def test_configuration_error():
    """Test ConfigurationError inherits from SdsOperatorError."""
    error = ConfigurationError("invalid config")
    assert str(error) == "invalid config"
    assert isinstance(error, SdsOperatorError)


def test_deployment_error_with_exit_code():
    """Test DeploymentError stores exit code and attempt number."""
    error = DeploymentError("deploy failed", exit_code=1, attempt=2)
    assert str(error) == "deploy failed"
    assert error.exit_code == 1
    assert error.attempt == 2
    assert isinstance(error, SdsOperatorError)


def test_deployment_error_without_optional_fields():
    """Test DeploymentError works without exit code or attempt."""
    error = DeploymentError("deploy failed")
    assert str(error) == "deploy failed"
    assert error.exit_code is None
    assert error.attempt is None


def test_filesystem_error():
    """Test FileSystemError inherits from SdsOperatorError."""
    error = FileSystemError("file not found")
    assert str(error) == "file not found"
    assert isinstance(error, SdsOperatorError)


def test_process_error_with_timeout():
    """Test ProcessError stores timeout flag and exit code."""
    error = ProcessError("process timeout", exit_code=-1, timeout=True)
    assert str(error) == "process timeout"
    assert error.timeout is True
    assert error.exit_code == -1
    assert isinstance(error, SdsOperatorError)


def test_process_error_without_timeout():
    """Test ProcessError works without timeout flag."""
    error = ProcessError("process failed", exit_code=1)
    assert str(error) == "process failed"
    assert error.timeout is False
    assert error.exit_code == 1


def test_agent_error():
    """Test AgentError inherits from SdsOperatorError."""
    error = AgentError("agent failed")
    assert str(error) == "agent failed"
    assert isinstance(error, SdsOperatorError)


def test_monitoring_error():
    """Test MonitoringError inherits from SdsOperatorError."""
    error = MonitoringError("unhealthy after deploy")
    assert str(error) == "unhealthy after deploy"
    assert isinstance(error, SdsOperatorError)


def test_exception_hierarchy():
    """Test all custom exceptions inherit from SdsOperatorError."""
    exceptions = [
        ConfigurationError("test"),
        DeploymentError("test"),
        FileSystemError("test"),
        ProcessError("test"),
        AgentError("test"),
        MonitoringError("test"),
    ]

    for exc in exceptions:
        assert isinstance(exc, SdsOperatorError)
        assert isinstance(exc, Exception)


def test_exceptions_can_be_caught_by_base():
    """Test that all exceptions can be caught using SdsOperatorError."""
    with pytest.raises(SdsOperatorError):
        raise ConfigurationError("test")

    with pytest.raises(SdsOperatorError):
        raise DeploymentError("test")

    with pytest.raises(SdsOperatorError):
        raise FileSystemError("test")

    with pytest.raises(SdsOperatorError):
        raise ProcessError("test")

    with pytest.raises(SdsOperatorError):
        raise AgentError("test")


def test_deployment_error_attributes_preserved():
    """Test DeploymentError attributes are preserved when caught."""
    with pytest.raises(DeploymentError, match="test") as exc_info:
        raise DeploymentError("test", exit_code=42, attempt=3)
    assert exc_info.value.exit_code == 42
    assert exc_info.value.attempt == 3


def test_process_error_timeout_attribute_preserved():
    """Test ProcessError timeout attribute is preserved when caught."""
    with pytest.raises(ProcessError, match="test") as exc_info:
        raise ProcessError("test", exit_code=-1, timeout=True)
    assert exc_info.value.timeout is True
    assert exc_info.value.exit_code == -1
