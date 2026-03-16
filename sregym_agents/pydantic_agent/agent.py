"""SREGym pydantic-ai agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent

from libs.pydantic_agent._base import BaseAgent

if TYPE_CHECKING:
    from pydantic_ai._run_context import RunContext
from sregym_agents.pydantic_agent.tools import (
    SREGymDeps,
    exec_bash,
    read_file,
    str_replace_file,
    submit_solution,
    write_file,
)

SYSTEM_PROMPT = """You are an SRE agent diagnosing and fixing issues in a Kubernetes application.

Use exec_bash to run kubectl, check logs, inspect resources, and apply fixes.
Use read_file / write_file / str_replace_file to work with local files.

Workflow:
1. DIAGNOSIS: investigate the cluster, then call submit_solution(ans="<natural language description>")
2. MITIGATION (if needed): apply the fix via kubectl, then call submit_solution(ans="")
"""


class SREGymAgent(BaseAgent[SREGymDeps]):
    def __init__(self, model: str, deps: SREGymDeps) -> None:
        super().__init__(deps)
        self._agent: Agent[SREGymDeps, str] = Agent(
            model,
            deps_type=SREGymDeps,
            output_type=str,
            tools=[exec_bash, read_file, write_file, str_replace_file, submit_solution],
        )

        @self._agent.instructions
        def _prompt(_: RunContext[SREGymDeps]) -> str:
            return SYSTEM_PROMPT

    def run(self, instruction: str) -> str:
        return self._run(instruction).output
