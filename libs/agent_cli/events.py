from typing import Protocol, Optional, Dict, Union


class AgentEventHandler(Protocol):
    """Protocol for handling agent events."""

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
