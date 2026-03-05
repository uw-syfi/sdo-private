from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from libs.agent_cli.trajectory import TrajectoryRecorderProtocol

AGENT_REGISTRY = {}


def register_provider(*names: str):
    """Decorator to register a coding agent provider.

    Args:
        *names: List of provider names/aliases (case-insensitive).
    """

    def decorator(cls):
        for name in names:
            AGENT_REGISTRY[name.lower()] = cls
        return cls

    return decorator


class CodingAgent(ABC):
    """Abstract base class for coding agents."""

    recorder: TrajectoryRecorderProtocol
    event_handler: Any | None = None

    def inject_mcp_server(self, repo_path: Path, sds_root: Path) -> None:
        """Inject MCP server configuration into the agent's settings for the target app.

        Args:
            repo_path: The target application directory.
            sds_root: The SDS project root.

        Raises:
            NotImplementedError: If the provider does not support MCP injection.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support MCP server injection")

    @abstractmethod
    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        """Generate text/code based on a prompt.

        Args:
            prompt: The prompt to send to the agent.
            cwd: Optional working directory context.
            timeout: Timeout in seconds.
            silent: If True, suppress stdout printing of the agent's output.

        Returns:
            Generated text.
        """
