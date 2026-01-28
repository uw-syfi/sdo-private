from pathlib import Path

from app_operator.trajectory import TrajectoryRecorderProtocol
from app_operator.adk.trajectory_plugin import AdkTrajectoryPlugin

# Assuming imports
try:
    from google.adk.agents import LlmAgent, LoopAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
except ImportError:
    # Fallback/Mock for development environment without ADK installed
    class Runner:
        def __init__(self, agent, app_name, session_service, plugins=None):
            pass

        def run(self, *args, **kwargs):
            pass

    class InMemorySessionService:
        pass

    class LlmAgent:
        pass

    class LoopAgent(LlmAgent):
        def __init__(self, name, sub_agents, max_iterations=None, tools=None):
            pass


class AdkAgentRunner:
    def __init__(
        self, app_name: str, recorder: TrajectoryRecorderProtocol, repo_path: Path
    ):
        self.app_name = app_name
        self.recorder = recorder
        self.repo_path = repo_path
        self.session_service = InMemorySessionService()

    def run_once(self, agent: LlmAgent, user_prompt: str) -> str:
        """Run the agent once with the given prompt and return the assistant response."""

        # Create runner with trajectory plugin
        runner = Runner(
            agent=agent,
            app_name=self.app_name,
            session_service=self.session_service,
            plugins=[AdkTrajectoryPlugin(self.recorder)],
        )

        # We need a unique session ID per run to avoid context pollution
        # or we reuse it?
        # Plan says: "Create InMemorySessionService() per operator run."
        # "Use runner.run_async(..., session_id=run_id, ...)"
        # If we use the same session_id, we maintain history.
        # Usually for phases like script generation vs deployment, we might want separate contexts?
        # But ADK Runner manages session.
        # The plan says: "Create InMemorySessionService() per operator run." (in __init__)
        # So we share history across the operator run?
        # AppOperator usually has distinct phases.
        # But `AdkOperator` uses `AdkAgentRunner.run_once`.
        # If we want fresh context, we should use different session_ids.
        # In `cli_agent`, each tool call (generate, fix) is usually a fresh conversation or carries relevant history.
        # `AdkOperator` plan says: "Create agents for each task ... On each LLM phase: run ADK agent via AdkAgentRunner.run_once".
        # If we use the same session_id, context grows.
        # Let's use a new session_id for each `run_once` call to ensure stateless behavior per phase/step
        # unless `AdkOperator` intends to keep history.
        # "run_once(self, agent: LlmAgent, user_prompt: str) -> str"
        # Usually implies single turn.

        # I'll use a new session_id for each call to ensure isolation, as typically
        # we pass full context in the prompt for SDS agents.
        # Or I can use `sds` as user_id and maybe a random session_id.
        import uuid

        session_id = str(uuid.uuid4())

        # Runner.run returns an event generator. Extract the final response text.
        response_text = ""

        if hasattr(runner, "run"):
            for event in runner.run(
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

        raise AttributeError("Runner does not provide a run() method")


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
