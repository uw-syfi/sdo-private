"""Agent-agnostic backend abstractions for the Crucible agent.

Public API:
    - ``AgentDriver`` — abstract LLM execution engine
    - ``AgentResult`` — typed run outcome
    - ``RunSubagent`` — callable protocol for subagent dispatch
    - ``ShortCircuitSignal`` — backend-agnostic interrupt data
    - ``PydanticAIDriver`` — pydantic-ai implementation of AgentDriver
    - ``AgentCLIDriver`` — agent-cli (Claude Code) implementation
    - ``SREAgent`` / ``JudgeAgent`` / ``RecoveryAgent`` — role agents
"""

from sregym_agents.crucible.backend.agent_cli_driver import AgentCLIDriver
from sregym_agents.crucible.backend.agents import (
    JudgeAgent,
    RecoveryAgent,
    SREAgent,
    SREAgentConfig,
)
from sregym_agents.crucible.backend.base import (
    AgentDriver,
    AgentResult,
    RunSubagent,
    ShortCircuitSignal,
)
from sregym_agents.crucible.backend.pydantic_ai_driver import PydanticAIDriver

__all__ = [
    "AgentCLIDriver",
    "AgentDriver",
    "AgentResult",
    "JudgeAgent",
    "PydanticAIDriver",
    "RecoveryAgent",
    "RunSubagent",
    "SREAgent",
    "SREAgentConfig",
    "ShortCircuitSignal",
]
