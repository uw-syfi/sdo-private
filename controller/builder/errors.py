class ControllerBuilderError(Exception):
    """Base exception for controller builder failures."""


class ToolRootError(ControllerBuilderError):
    """Raised when the checker cannot locate controller tooling."""
