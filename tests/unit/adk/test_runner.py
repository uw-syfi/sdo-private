import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
from app_operator.adk.runner import AdkAgentRunner
from app_operator.trajectory import NullTrajectoryRecorder


def test_run_async_basic():
    recorder = NullTrajectoryRecorder()
    runner_wrapper = AdkAgentRunner("app", recorder, Path("."))

    # Agent is expected to be fully constructed (e.g. by build_adk_agent)
    agent = MagicMock()

    with patch("app_operator.adk.runner.Runner") as MockRunner:
        mock_instance = MockRunner.return_value
        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.content = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "Response text"
        mock_event.content.parts = [mock_part]

        async def mock_run_async(*args, **kwargs):
            yield mock_event

        mock_instance.run_async = mock_run_async

        result = asyncio.run(runner_wrapper.run_async(agent, "Hello"))
        assert result == "Response text"


def test_runner_initializes_plugins():
    recorder = MagicMock()
    runner_wrapper = AdkAgentRunner("app", recorder, Path("."))
    agent = MagicMock()

    with patch("app_operator.adk.runner.Runner") as MockRunner:
        mock_instance = MockRunner.return_value

        async def mock_run_async(*args, **kwargs):
            # Must yield something to complete iteration
            event = MagicMock()
            event.is_final_response.return_value = True
            yield event

        mock_instance.run_async = mock_run_async

        asyncio.run(runner_wrapper.run_async(agent, "Hello"))

        args, kwargs = MockRunner.call_args
        assert "plugins" in kwargs
        plugins = kwargs["plugins"]
        assert len(plugins) == 1
        # Check type name since we might not have reference to class if imports fail
        assert type(plugins[0]).__name__ == "AdkTrajectoryPlugin"
        assert plugins[0].recorder == recorder
