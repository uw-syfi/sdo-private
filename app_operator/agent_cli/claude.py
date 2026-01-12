from typing import Optional, List

from .cli_agent import CLICodingAgent


class ClaudeCodeCodingAgent(CLICodingAgent):
    """Coding agent implementation using the Claude Code CLI tool."""

    def __init__(self, model: Optional[str] = None):
        """Initialize the Claude Code coding agent.

        Args:
            model: Optional model name to use with Claude Code. If None, uses default.
        """
        super().__init__("claude", model)

    @property
    def claude_path(self) -> str:
        """Return path to claude binary (for backward compatibility)."""
        return self.binary_path

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return "[Claude]"

    def _get_command(self, prompt: str) -> List[str]:
        cmd = [
            self.binary_path,
            "-p",  # Print mode, accepts prompt from stdin
            "--dangerously-skip-permissions"  # Auto-approval mode
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd
