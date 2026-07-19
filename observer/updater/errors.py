class ObserverUpdaterError(Exception):
    """Base exception for observer updater failures."""


class ToolRootError(ObserverUpdaterError):
    """Raised when the checker cannot locate observer tooling."""
