from typing import Protocol


class OperatorUI(Protocol):
    def set_stage(self, stage: str, detail: str | None = None, status: str | None = None) -> None:
        """Update the current operating stage."""
        ...

    def log(self, message: str, level: str = "info") -> None:
        """Log a message to the UI."""
        ...

    def on_thinking(self, text: str) -> None:
        """Handle agent thinking output."""
        ...

    def on_tool_call(self, tool: str, args: dict | str | None = None) -> None:
        """Handle tool execution start."""
        ...

    def on_tool_result(
        self,
        tool: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        duration: float | None = None,
    ) -> None:
        """Handle tool execution result."""
        ...

    def close(self, status: str | None = None, exit_code: int | None = None) -> None:
        """Close the UI."""
        ...


class NullOperatorUI:
    """No-op implementation of OperatorUI."""

    def set_stage(self, stage: str, detail: str | None = None, status: str | None = None) -> None:
        pass

    def log(self, message: str, level: str = "info") -> None:
        pass

    def on_thinking(self, text: str) -> None:
        pass

    def on_tool_call(self, tool: str, args: dict | str | None = None) -> None:
        pass

    def on_tool_result(
        self,
        tool: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        duration: float | None = None,
    ) -> None:
        pass

    def close(self, status: str | None = None, exit_code: int | None = None) -> None:
        pass
