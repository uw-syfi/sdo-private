"""Crucible agents and driver abstractions.

Public API:
    - ``AgentDriver`` — abstract LLM execution engine
    - ``AgentResult`` — typed run outcome
    - ``RunSubagent`` — callable protocol for subagent dispatch
    - ``ShortCircuitSignal`` — backend-agnostic interrupt data
    - ``PydanticAIDriver`` — pydantic-ai implementation of AgentDriver
    - ``AgentCLIDriver`` — agent-cli (Claude Code) implementation
    - ``SREAgent`` / ``JudgeAgent`` / ``RecoveryAgent`` — role agents
"""

from sregym_agents.crucible.agents.base import (
    AgentDriver,
    AgentResult,
    RunSubagent,
    ShortCircuitSignal,
)
from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver
from sregym_agents.crucible.agents.drivers.pydantic_ai_driver import PydanticAIDriver
from sregym_agents.crucible.agents.judge_agent import JudgeAgent
from sregym_agents.crucible.agents.recovery_agent import RecoveryAgent, RecoveryRunResult
from sregym_agents.crucible.agents.sre_agent import SREAgent, SREAgentConfig

__all__ = [
    "AgentCLIDriver",
    "AgentDriver",
    "AgentResult",
    "JudgeAgent",
    "PydanticAIDriver",
    "RecoveryAgent",
    "RecoveryRunResult",
    "RunSubagent",
    "SREAgent",
    "SREAgentConfig",
    "ShortCircuitSignal",
]
