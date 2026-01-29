import unittest
import asyncio
from unittest.mock import MagicMock, patch
from pathlib import Path
from app_operator.adk.runner import AdkAgentRunner, LlmAgent


class TestAdkAgentRunner(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app_name = "test_app"
        self.recorder = MagicMock()
        self.repo_path = Path("/repo")
        self.runner = AdkAgentRunner(app_name=self.app_name, recorder=self.recorder, repo_path=self.repo_path)

    async def test_run_async_basic(self):
        # Create a mock agent with some tools
        mock_tool = MagicMock(return_value="tool_output")
        mock_tool.__name__ = "mock_tool"
        agent = LlmAgent(name="TestAgent", tools=[mock_tool])

        # Mock the ADK Runner class and its instance
        with (
            patch("app_operator.adk.runner.Runner") as MockRunnerCls,
            patch("app_operator.adk.runner.AdkTrajectoryPlugin"),
        ):
            # Setup the mock instance returned by Runner(...)
            mock_runner_instance = MockRunnerCls.return_value

            # Setup the events yielded by run_async
            # Event structure based on code: event.content.parts[0].text
            mock_event1 = MagicMock()
            mock_event1.content.parts = [MagicMock(text="Part 1")]
            mock_event1.is_final_response.return_value = False

            mock_event2 = MagicMock()
            mock_event2.content.parts = [MagicMock(text="Part 2")]
            mock_event2.is_final_response.return_value = True

            # Mock run_async to return an async iterator
            async def async_gen(*args, **kwargs):
                yield mock_event1
                yield mock_event2

            mock_runner_instance.run_async = async_gen

            # Run the method under test
            response = await self.runner.run_async(agent, "User Prompt")

            # Assertions
            # The runner implementation overwrites the response text with the latest event text
            self.assertEqual(response, "Part 2")

            # Verify Runner was initialized correctly
            MockRunnerCls.assert_called_once()
            call_args = MockRunnerCls.call_args[1]
            self.assertEqual(call_args["agent"], agent)
            self.assertEqual(call_args["app_name"], self.app_name)

    async def test_run_async_no_response(self):
        agent = LlmAgent(name="TestAgent")

        with (
            patch("app_operator.adk.runner.Runner") as MockRunnerCls,
            patch("app_operator.adk.runner.AdkTrajectoryPlugin"),
        ):
            mock_runner_instance = MockRunnerCls.return_value

            async def async_gen(*args, **kwargs):
                if False:
                    yield  # empty generator

            mock_runner_instance.run_async = async_gen

            response = await self.runner.run_async(agent, "prompt")
            self.assertEqual(response, "")

    async def test_extract_text_from_event(self):
        # We can test _extract_text_from_event by invoking run_async with mocked events
        # or we can test the helper directly if we import it.
        from app_operator.adk.runner import _extract_text_from_event

        event = MagicMock()
        event.content.parts = [
            MagicMock(text="Hello"),
            MagicMock(text=" "),
            MagicMock(text="World"),
        ]

        text = _extract_text_from_event(event)
        self.assertEqual(text, "Hello World")

        # Test empty content
        event_empty = MagicMock()
        event_empty.content = None
        self.assertEqual(_extract_text_from_event(event_empty), "")
