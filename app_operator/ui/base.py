from typing import Protocol, Optional, Dict, Union


class OperatorUI(Protocol):
    def set_stage(
        self, stage: str, detail: Optional[str] = None, status: Optional[str] = None
    ) -> None:
        """Update the current operating stage."""
        ...

    def log(self, message: str, level: str = "info") -> None:
        """Log a message to the UI."""
        ...

    def on_thinking(self, text: str) -> None:
        """Handle agent thinking output."""
        ...

    def on_tool_call(self, tool: str, args: Optional[Union[Dict, str]] = None) -> None:
        """Handle tool execution start."""
        ...

    def on_tool_result(
        self,
        tool: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        """Handle tool execution result."""
        ...

    def close(
        self, status: Optional[str] = None, exit_code: Optional[int] = None
    ) -> None:
        """Close the UI."""
        ...


class NullOperatorUI:
    """No-op implementation of OperatorUI."""

    def set_stage(
        self, stage: str, detail: Optional[str] = None, status: Optional[str] = None
    ) -> None:
        pass

    def log(self, message: str, level: str = "info") -> None:
        pass

    def on_thinking(self, text: str) -> None:
        pass

    def on_tool_call(self, tool: str, args: Optional[Union[Dict, str]] = None) -> None:
        pass

    def on_tool_result(
        self,
        tool: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        pass

    def close(
        self, status: Optional[str] = None, exit_code: Optional[int] = None
    ) -> None:
        pass
