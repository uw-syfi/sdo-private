"""Custom exceptions for SDS Operator.

This module defines a hierarchy of exceptions used throughout the operator
to provide clear error categories and better error messages with context.
"""


class SdsOperatorError(Exception):
    """Base exception for all operator errors."""

    pass


class ConfigurationError(SdsOperatorError):
    """Configuration-related errors.

    Raised when configuration values are invalid or configuration files
    cannot be loaded properly.
    """

    pass


class DeploymentError(SdsOperatorError):
    """Deployment-related errors.

    Raised when deployment operations fail.
    """

    def __init__(self, message: str, exit_code: int = None, attempt: int = None):
        """Initialize deployment error with context.

        Args:
            message: Error message describing the failure.
            exit_code: Optional exit code from the failed deployment.
            attempt: Optional attempt number when the error occurred.
        """
        super().__init__(message)
        self.exit_code = exit_code
        self.attempt = attempt


class FileSystemError(SdsOperatorError):
    """Filesystem operation errors.

    Raised when file operations (read, write, chmod, mkdir) fail.
    """

    pass


class ProcessError(SdsOperatorError):
    """Process execution errors.

    Raised when subprocess operations fail or timeout.
    """

    def __init__(self, message: str, exit_code: int = None, timeout: bool = False):
        """Initialize process error with context.

        Args:
            message: Error message describing the failure.
            exit_code: Optional exit code from the failed process.
            timeout: Whether the error was caused by a timeout.
        """
        super().__init__(message)
        self.exit_code = exit_code
        self.timeout = timeout


class AgentError(SdsOperatorError):
    """Agent-related errors.

    Raised when the coding agent fails to generate, fix, or analyze.
    """

    pass
