from pathlib import Path
from unittest.mock import MagicMock, patch
from app_operator.adk.runner import AdkAgentRunner
from app_operator.trajectory import NullTrajectoryRecorder


def test_runner_returns_text():
    recorder = NullTrajectoryRecorder()
    runner_wrapper = AdkAgentRunner("app", recorder, Path("."))

    agent = MagicMock()

    with patch("app_operator.adk.runner.Runner") as MockRunner:
        mock_instance = MockRunner.return_value
        mock_result = MagicMock()
        mock_result.text = "Response text"

        # Mock run_async
        async def mock_run_async(*args, **kwargs):
            return mock_result

        mock_instance.run_async = mock_run_async

        # We need to ensure asyncio.run works.
        # If imports of google.genai.agent failed, AdkAgentRunner uses fallback classes.
        # But we patched Runner, so it uses our mock.

        result = runner_wrapper.run_once(agent, "Hello")
        assert result == "Response text"
