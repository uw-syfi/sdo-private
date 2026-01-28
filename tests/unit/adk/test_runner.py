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
        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.content = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "Response text"
        mock_event.content.parts = [mock_part]

        mock_instance.run.return_value = iter([mock_event])

        result = runner_wrapper.run_once(agent, "Hello")
        assert result == "Response text"
