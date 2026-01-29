import asyncio
import functools
import uuid
from pathlib import Path
from typing import Any, Callable, List, Optional

from app_operator.trajectory import TrajectoryRecorderProtocol
from app_operator.adk.trajectory_plugin import AdkTrajectoryPlugin

# Assuming imports
try:
    from google.adk.agents import LlmAgent, LoopAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
except ImportError:
    # Fallback/Mock for development environment without ADK installed
    class LlmAgent:
        def __init__(
            self,
            model=None,
            tools=None,
            instructions=None,
            name=None,
            description=None,
        ):
            self.tools = tools or []
            self.sub_agents = []

    class LoopAgent(LlmAgent):
        def __init__(self, name, sub_agents, max_iterations=None, tools=None):
            super().__init__(name=name, tools=tools)
            self.sub_agents = sub_agents

    class Runner:
        def __init__(self, agent, app_name, session_service, plugins=None):
            pass

        def run(self, *args, **kwargs):
            yield type(
                "Event",
                (),
                {
                    "content": type(
                        "Content",
                        (),
                        {"parts": [type("Part", (), {"text": "mock response"})]},
                    ),
                    "is_final_response": lambda: True,
                },
            )

        async def run_async(self, *args, **kwargs):
            yield type(
                "Event",
                (),
                {
                    "content": type(
                        "Content",
                        (),
                        {"parts": [type("Part", (), {"text": "mock response"})]},
                    ),
                    "is_final_response": lambda: True,
                },
            )

    class InMemorySessionService:
        pass


class AdkAgentRunner:
    def __init__(
        self, app_name: str, recorder: TrajectoryRecorderProtocol, repo_path: Path
    ):
        self.app_name = app_name
        self.recorder = recorder
        self.repo_path = repo_path
        self.session_service = InMemorySessionService()

    async def run_async(self, agent: LlmAgent, user_prompt: str) -> str:
        """Run the agent asynchronously once with the given prompt and return the assistant response."""

        # Ensure tools are async-compatible
        self._asyncify_agent_tools(agent)

        # Create runner with trajectory plugin
        runner = Runner(
            agent=agent,
            app_name=self.app_name,
            session_service=self.session_service,
            plugins=[AdkTrajectoryPlugin(self.recorder)],
        )

        session_id = str(uuid.uuid4())
        response_text = ""

        if hasattr(runner, "run_async"):
            async for event in runner.run_async(
                user_id="sds", session_id=session_id, new_message=user_prompt
            ):
                # Prefer the final response event text; fallback to last text seen.
                event_text = _extract_text_from_event(event)
                if event_text:
                    response_text = event_text
                if hasattr(event, "is_final_response") and event.is_final_response():
                    if event_text:
                        response_text = event_text
                    break
            return response_text

        raise AttributeError("Runner does not provide a run_async() method")

    def _asyncify_agent_tools(self, agent: LlmAgent) -> None:
        """Ensure all tools on the agent (and sub-agents) are async wrappers."""
        if hasattr(agent, "tools") and agent.tools:
            new_tools = []
            for tool in agent.tools:
                if asyncio.iscoroutinefunction(tool):
                    new_tools.append(tool)
                else:
                    new_tools.append(self._wrap_tool_async(tool))
            agent.tools = new_tools

        # Handle LoopAgent sub-agents
        if hasattr(agent, "sub_agents") and agent.sub_agents:
            for sub in agent.sub_agents:
                self._asyncify_agent_tools(sub)

    def _wrap_tool_async(self, tool: Callable) -> Callable:
        """Wrap a synchronous tool function to run in a thread."""

        @functools.wraps(tool)
        async def wrapper(*args, **kwargs):
            return await asyncio.to_thread(tool, *args, **kwargs)

        return wrapper


def _extract_text_from_event(event: object) -> str:
    """Extract plain text content from an ADK event."""
    content = getattr(event, "content", None)
    if content is None:
        return ""
    parts = getattr(content, "parts", None) or []
    texts = []
    for part in parts:
        text = getattr(part, "text", None)
        if text:
            texts.append(text)
    return "".join(texts)
