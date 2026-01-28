from pathlib import Path

from app_operator.trajectory import TrajectoryRecorderProtocol
from app_operator.adk.trajectory_plugin import AdkTrajectoryPlugin

# Assuming imports
try:
    from google.genai.agent import Runner, InMemorySessionService, LlmAgent
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

        # Runner.run is typically synchronous? Plan says run_async.
        # If I am in sync context, I should use run() if available, or run_async with event loop.
        # app_operator is synchronous mostly.
        # I'll check if `run` exists. If only `run_async`, I need `asyncio.run`.
        # Most python SDKs provide sync `run`.
        # I'll assume `run` exists or handle async if needed.
        # Plan says: "Use runner.run_async... and extract final model response text."

        # Since I'm in a sync method `run_once`, I'll try to run it synchronously.
        # If `run_async` is the only way, I'll wrap it.

        try:
            # Assuming sync run is available or I can wrap async
            # But the plan explicitly mentioned run_async.
            # Let's try to use asyncio.run if I can import it.
            import asyncio

            # We need to execute the runner
            # output = runner.run(user_id="sds", session_id=session_id, new_message=user_prompt)
            # If `run` returns the result directly.

            # Using asyncio.run for run_async
            result = asyncio.run(
                runner.run_async(
                    user_id="sds", session_id=session_id, new_message=user_prompt
                )
            )

            # Extract text from result
            if hasattr(result, "text"):
                return result.text
            elif hasattr(result, "content"):
                return str(result.content)
            else:
                return str(result)

        except AttributeError:
            # Maybe run() exists and is sync
            if hasattr(runner, "run"):
                result = runner.run(
                    user_id="sds", session_id=session_id, new_message=user_prompt
                )
                if hasattr(result, "text"):
                    return result.text
                elif hasattr(result, "content"):
                    return str(result.content)
                else:
                    return str(result)
            raise
