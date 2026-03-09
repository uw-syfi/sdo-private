from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from app_operator.cli_agent.agents.context import AgentContext

T = TypeVar("T")


class OperatorAgent(ABC, Generic[T]):
    """Base class encoding: prepare -> execute -> parse."""

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    @abstractmethod
    def prepare(self, **kwargs) -> str:
        """Build the prompt. All pre-agent logic lives here."""

    def execute(self, prompt: str, timeout: int | None = None) -> str:
        """Call the LLM. Override for retries or custom behavior."""
        return self.ctx.coding_agent.generate(
            prompt,
            cwd=str(self.ctx.repo_path),
            timeout=timeout or self.ctx.operator_config.agent_timeout,
        )

    @abstractmethod
    def parse(self, response: str) -> T:
        """Extract structured result from raw response."""

    def run(self, **kwargs) -> T:
        """Template: prepare -> execute -> parse."""
        prompt = self.prepare(**kwargs)
        response = self.execute(prompt)
        return self.parse(response)
