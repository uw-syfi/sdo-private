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

from benchmarks.sregym.agents.crucible.agents.base import (
    AgentDriver,
    AgentResult,
    RunSubagent,
    ShortCircuitSignal,
)
from benchmarks.sregym.agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver
from benchmarks.sregym.agents.crucible.agents.drivers.pydantic_ai_driver import PydanticAIDriver
from benchmarks.sregym.agents.crucible.agents.judge_agent import JudgeAgent
from benchmarks.sregym.agents.crucible.agents.recovery_agent import RecoveryAgent
from benchmarks.sregym.agents.crucible.agents.sre_agent import SREAgent, SREAgentConfig

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
