import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app_operator.adk.trajectory_plugin import AdkTrajectoryPlugin
from app_operator.trajectory import TrajectoryRecorderProtocol


class AdkAgentRunner:
    def __init__(
        self,
        app_name: str,
        repo_path: Path,
        recorder: TrajectoryRecorderProtocol | None = None,
    ):
        self.app_name = app_name
        self.recorder = recorder
        self.repo_path = repo_path
        self.session_service = InMemorySessionService()

    async def run_async(
        self,
        agent: LlmAgent,
        user_prompt: str,
        on_event: Callable[[Any], None] | None = None,
    ) -> str:
        """Run the agent asynchronously once with the given prompt and return the assistant response."""

        plugins = []
        if self.recorder:
            plugins.append(AdkTrajectoryPlugin(self.recorder))

        # Create runner with trajectory plugin
        runner = Runner(
            agent=agent,
            app_name=self.app_name,
            session_service=self.session_service,
            plugins=plugins,
        )

        session_id = str(uuid.uuid4())

        # Create session explicitly
        await self.session_service.create_session(app_name=self.app_name, user_id="sds", session_id=session_id)

        response_text = ""

        if hasattr(runner, "run_async"):
            content = types.Content(role="user", parts=[types.Part(text=user_prompt)])
            async for event in runner.run_async(user_id="sds", session_id=session_id, new_message=content):
                if on_event:
                    on_event(event)
                # Accumulate text from events (deltas)
                event_text = _extract_text_from_event(event)
                if event_text:
                    response_text += event_text

                if hasattr(event, "is_final_response") and event.is_final_response():
                    break
            return response_text

        raise AttributeError("Runner does not provide a run_async() method")


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
