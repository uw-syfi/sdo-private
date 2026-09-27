from __future__ import annotations

from typing import TYPE_CHECKING

from .base import register_provider
from .cli_agent import CLICodingAgent
from .mcp_config import HttpMcpServer, McpServerConfig

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .events import AgentEventHandler
    from .sandbox import SandboxConfig
    from .trajectory import TrajectoryRecorderProtocol


def _build_mcp_args(mcp_servers: Sequence[McpServerConfig]) -> list[str]:
    args: list[str] = []
    for server in mcp_servers:
        prefix = f"mcp_servers.{server.name}"
        if isinstance(server, HttpMcpServer):
            args.extend(["-c", f'{prefix}.url="{server.url}"'])
        else:
            args.extend(["-c", f'{prefix}.command="{server.command}"'])
            if server.args:
                toml_args = "[" + ", ".join(f'"{arg}"' for arg in server.args) + "]"
                args.extend(["-c", f"{prefix}.args={toml_args}"])
            for key, value in server.env.items():
                args.extend(["-c", f'{prefix}.env.{key}="{value}"'])
    return args


@register_provider("openai", "codex")
class CodexCodingAgent(CLICodingAgent):
    """Coding agent implementation using the Codex CLI tool."""

    def __init__(
        self,
        model: str | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        event_handler: AgentEventHandler | None = None,
        mcp_servers: list[McpServerConfig] | None = None,
        sandbox: bool | SandboxConfig = False,
    ):
        """Initialize the Codex coding agent.

        Args:
            model: Optional model name to use with codex. If None, uses default.
            mcp_servers: Optional list of MCP server configurations.
            sandbox: Not supported for Codex; must be False.
        """
        if sandbox:
            raise NotImplementedError("sandbox is not supported for CodexCodingAgent")
        super().__init__("codex", model, recorder, event_handler, mcp_servers=mcp_servers)

    @property
    def codex_path(self) -> str:
        """Return path to codex binary (for backward compatibility)."""
        return self.binary_path

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return "[Codex]"

    def _build_mcp_args(self) -> list[str]:
        """Build -c flag arguments for MCP server configuration."""
        return _build_mcp_args(self.mcp_servers)

    def _get_command(self, prompt: str) -> list[str]:
        cmd = [self.binary_path, "exec", "--dangerously-bypass-approvals-and-sandbox"]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.mcp_servers:
            cmd.extend(self._build_mcp_args())
        return cmd
