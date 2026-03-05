from .base import register_provider
from .cli_agent import CLICodingAgent


@register_provider("openai", "codex")
class CodexCodingAgent(CLICodingAgent):
    """Coding agent implementation using the Codex CLI tool."""

    def __init__(self, model: str | None = None):
        """Initialize the Codex coding agent.

        Args:
            model: Optional model name to use with codex. If None, uses default.
        """
        super().__init__("codex", model)

    @property
    def codex_path(self) -> str:
        """Return path to codex binary (for backward compatibility)."""
        return self.binary_path

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return "[Codex]"

    def _get_command(self, prompt: str) -> list[str]:
        cmd = [self.binary_path, "exec", "--dangerously-bypass-approvals-and-sandbox"]
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd
