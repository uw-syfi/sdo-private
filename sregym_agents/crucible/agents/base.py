"""Agent-agnostic execution abstractions for the Crucible backend.

Defines the ``AgentDriver`` ABC (generic LLM execution), ``AgentResult``
(typed run outcome), ``RunSubagent`` (callable protocol for subagent
dispatch), and ``ShortCircuitSignal`` (backend-agnostic interrupt data).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@dataclass
class AgentResult(Generic[T]):
    """Outcome of a single ``AgentDriver.run()`` invocation.

    Attributes:
        output: Typed result (``None`` when the run failed or was interrupted
            before producing output).
        completed: ``False`` when the run was interrupted (short-circuit,
            model error, usage limit) before normal completion.
        interrupt_data: Opaque payload from an interruption — e.g. an
            ``LTMShortCircuit`` exception or a socket signal dict.
        messages: Raw message history from the underlying backend, suitable
            for passing back as ``message_history`` in a follow-on call.
    """

    output: T | None = None
    completed: bool = True
    interrupt_data: Any = None
    messages: list[Any] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    def unwrap(self, agent_name: str = "") -> T:
        """Return output or raise if the run didn't complete successfully.

        Args:
            agent_name: Optional agent name for a more informative error message.
        """
        if not self.completed or self.output is None:
            label = f"Agent {agent_name!r}" if agent_name else "Agent"
            raise RuntimeError(f"{label} did not produce output")
        return self.output


class AgentDriver(ABC):
    """Abstract LLM execution engine — no crucible knowledge.

    Implementations wrap a concrete backend (pydantic-ai, agent-cli, etc.)
    and expose a single ``run()`` entry point.
    """

    @abstractmethod
    async def run(
        self,
        *,
        prompt: str,
        system_prompt: str = "",
        tools: list[Any] | None = None,
        output_type: type[T] = str,  # type: ignore[assignment]
        agent_name: str = "",
        timeout: int | None = None,
        model_settings: dict[str, Any] | None = None,
        message_history: list[Any] | None = None,
        usage_collector: Any | None = None,
        **kwargs: Any,
    ) -> AgentResult[T]:
        """Execute a single LLM invocation and return a typed result.

        Parameters:
            prompt: User prompt text.
            system_prompt: System prompt (instructions).
            tools: List of tool callables to register.
            output_type: Expected output type (``str`` for free-form text).
            agent_name: Human-readable name for logging / trajectory.
            timeout: Optional per-run timeout in seconds.
            model_settings: Backend-specific model configuration (e.g.
                thinking budget, max_tokens).
            message_history: Previous messages for conversation continuity.
            usage_collector: ``UsageCollector`` instance for token accounting.
            **kwargs: Backend-specific extras (e.g. ``deps``, ``run_ctx``
                for PydanticAIDriver).
        """
        ...


@dataclass(frozen=True)
class ShortCircuitSignal:
    """Backend-agnostic representation of a short-circuit interrupt.

    Created by the orchestrator from ``AgentResult.interrupt_data`` —
    works the same whether the interrupt came from a caught exception
    (pydantic-ai) or a socket signal (agent-cli).

    Attributes:
        confirmed: List of confirmed candidates / applied mitigations.
        iteration: Iteration number when the short-circuit occurred.
        confirmed_slugs: KB class slugs for the confirmed candidates
            (diagnosis only).
        stage: ``"diagnosis"`` or ``"mitigation"``.
    """

    confirmed: list[str]
    iteration: int
    confirmed_slugs: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    stage: str = "diagnosis"


@runtime_checkable
class RunSubagent(Protocol):
    """Callable protocol for subagent dispatch.

    Returns ``T`` directly — subagents always expect a result; failures
    raise exceptions. This lives alongside ``AgentDriver`` but is a
    separate concern: it wraps ``driver.run()`` and unwraps
    ``result.output``, raising on failure.
    """

    async def __call__(
        self,
        *,
        prompt: str,
        output_type: type[T],
        tools: list[Any] | None = None,
        agent_name: str = "",
        model_settings: dict[str, Any] | None = None,
        usage_collector: Any | None = None,
    ) -> T: ...
