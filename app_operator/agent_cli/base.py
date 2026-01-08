from abc import ABC, abstractmethod
from typing import Optional


class CodingAgent(ABC):
    """Abstract base class for coding agents."""

    @abstractmethod
    def generate(self, prompt: str,
                 cwd: Optional[str] = None, timeout: int = 300) -> str:
        """Generate text/code based on a prompt.

        Args:
            prompt: The prompt to send to the agent.
            cwd: Optional working directory context.
            timeout: Timeout in seconds.

        Returns:
            Generated text.
        """
        pass
