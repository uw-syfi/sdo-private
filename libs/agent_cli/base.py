from abc import ABC, abstractmethod
from typing import Optional, Any

from app_operator.trajectory import TrajectoryRecorderProtocol


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
    event_handler: Optional[Any] = None

    @abstractmethod
    def generate(
        self,
        prompt: str,
        cwd: Optional[str] = None,
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
        pass
