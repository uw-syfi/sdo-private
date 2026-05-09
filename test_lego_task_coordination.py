"""
Unit tests for lego_agent task coordination fix.

Tests that the completion signal properly flows through iterative_discovery → fan_out → summarize.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from lego_agent.backend.pipeline_builder import (
    _make_agent_worker,
    _make_fan_out_worker,
    _make_summarize_worker,
)
from lego_agent.backend.queue_runtime import Task


class TestTaskCoordinationFix(unittest.TestCase):
    """Test that completion signals flow properly through the pipeline."""

    def test_iterative_discovery_emits_completion_sentinel(self):
        """Test that iterative_discovery yields a STEP_COMPLETE sentinel after items."""

        async def run_test():
            # Create a mock agent that yields mock items
            mock_agent = MagicMock()

            # Simulate discovering 3 items in one batch, then DONE
            mock_agent.generate_async = AsyncMock(side_effect=["item1.py\nitem2.py\nitem3.py\nDONE"])

            config = {
                "instruction": "Find Python files",
                "tools": [],
                "emit_mode": "iterative_discovery",
                "batch_size": 15,
            }

            # Patch create_agent to return our mock
            with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
                worker = _make_agent_worker(config)

                # Create an initial task
                task = Task.create("start", {"input": ""})

                # Collect all yielded tasks
                yielded_tasks = [yielded_task async for yielded_task in worker(task)]

                # Verify we got 3 items + 1 completion sentinel
                self.assertEqual(len(yielded_tasks), 4, "Should have 3 items + 1 completion sentinel")

                # Verify the first 3 are agent_output tasks
                for i in range(3):
                    self.assertEqual(yielded_tasks[i].type, "agent_output")
                    self.assertIn("item", str(yielded_tasks[i].payload))

                # Verify the last one is the completion sentinel
                self.assertEqual(yielded_tasks[-1].type, "STEP_COMPLETE")
                self.assertTrue(yielded_tasks[-1].metadata.get("is_completion_signal"))
                self.assertEqual(yielded_tasks[-1].payload.get("count"), 3)
                print("✓ Iterative discovery correctly emits completion sentinel")

        asyncio.run(run_test())

    def test_fan_out_passes_through_completion_signal(self):
        """Test that fan_out passes through completion signals."""

        async def run_test():
            mock_agent = MagicMock()
            mock_agent.generate_async = AsyncMock(return_value="summary of item")

            config = {
                "agent": {
                    "instruction": "Summarize {input}",
                    "tools": [],
                },
                "max_workers": 4,
            }

            with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
                worker = _make_fan_out_worker(config)

                # Create a completion sentinel task
                completion_task = Task.create(
                    "STEP_COMPLETE",
                )

                # Collect yielded tasks
                yielded_tasks = [yielded_task async for yielded_task in worker(completion_task)]

                # Should pass through the completion signal unchanged
                self.assertEqual(len(yielded_tasks), 1)
                self.assertEqual(yielded_tasks[0].type, "STEP_COMPLETE")
                self.assertEqual(yielded_tasks[0].payload.get("source"), "iterative_discovery")
                print("✓ Fan_out correctly passes through completion signal")

        asyncio.run(run_test())

    def test_fan_out_processes_normal_items(self):
        """Test that fan_out processes normal items when not a completion signal."""

        async def run_test():
            mock_agent = MagicMock()
            mock_agent.generate_async = AsyncMock(return_value="summary of file1.py")

            config = {
                "agent": {
                    "instruction": "Summarize {input}",
                    "tools": [],
                },
                "max_workers": 4,
            }

            with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
                worker = _make_fan_out_worker(config)

                # Create a normal task with a single file path
                task = Task.create("agent_output", {"result": "file1.py"})

                # Collect yielded tasks
                yielded_tasks = [yielded_task async for yielded_task in worker(task)]

                # Should yield one fan_out_output task
                self.assertEqual(len(yielded_tasks), 1)
                self.assertEqual(yielded_tasks[0].type, "fan_out_output")
                self.assertIn("summary", yielded_tasks[0].payload.get("result", ""))
                print("✓ Fan_out correctly processes normal items")

        asyncio.run(run_test())

    def test_summarize_buffers_items_and_waits_for_completion(self):
        """Test that summarize buffers items and only processes on completion signal."""

        async def run_test():
            mock_agent = MagicMock()
            mock_agent.generate_async = AsyncMock(return_value="# All Summaries\n\nFile summaries combined.")

            config = {
                "instruction": "Combine summaries",
                "agent": {
                    "tools": [],
                },
            }

            with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
                worker = _make_summarize_worker(config)

                # Create 3 fan_out_output tasks with summaries
                task1 = Task.create("fan_out_output", {"result": "Summary of file1.py"})
                task2 = Task.create("fan_out_output", {"result": "Summary of file2.py"})
                task3 = Task.create("fan_out_output", {"result": "Summary of file3.py"})

                # Process them - should get empty returns (buffering)
                result1 = await worker(task1)
                result2 = await worker(task2)
                result3 = await worker(task3)

                self.assertEqual(len(result1), 0, "First item should return empty list (buffering)")
                self.assertEqual(len(result2), 0, "Second item should return empty list (buffering)")
                self.assertEqual(len(result3), 0, "Third item should return empty list (buffering)")

                # Create completion signal
                completion_task = Task.create(
                    "STEP_COMPLETE", {"source": "fan_out", "count": 3}, metadata={"is_completion_signal": True}
                )

                # Process completion - should now generate summary with all buffered items
                result_completion = await worker(completion_task)

                # Should now produce one summarize_output task
                self.assertEqual(len(result_completion), 1)
                self.assertEqual(result_completion[0].type, "summarize_output")

                # Verify the LLM was called with combined content
                mock_agent.generate_async.assert_called_once()
                call_args = mock_agent.generate_async.call_args
                prompt = call_args[0][0]

                # Check that all summaries are in the prompt
                self.assertIn("Summary of file1.py", prompt)
                self.assertIn("Summary of file2.py", prompt)
                self.assertIn("Summary of file3.py", prompt)
                print("✓ Summarize correctly buffers items and processes on completion signal")

        asyncio.run(run_test())

    def test_summarize_without_completion_signal_before(self):
        """Test that summarize doesn't process until receiving completion signal."""

        async def run_test():
            mock_agent = MagicMock()
            mock_agent.generate_async = AsyncMock(return_value="Combined summary")

            config = {
                "instruction": "Combine summaries",
                "agent": {
                    "tools": [],
                },
            }

            with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
                worker = _make_summarize_worker(config)

                # Process many items - all should buffer
                for i in range(10):
                    task = Task.create("fan_out_output", {"result": f"Summary {i}"})
                    result = await worker(task)
                    self.assertEqual(len(result), 0, f"Item {i} should buffer, not process")

                # Agent should not have been called yet
                mock_agent.generate_async.assert_not_called()
                print("✓ Summarize correctly waits and doesn't process prematurely")

        asyncio.run(run_test())

    def test_fan_out_waits_for_all_workers_before_passing_completion(self):
        """Test that fan_out waits for all async workers to complete before passing completion signal."""

        async def run_test():
            # Create a mock agent that introduces delays to simulate async work
            call_count = {"count": 0}

            async def delayed_generate(prompt: str, timeout: int) -> str:
                call_count["count"] += 1
                await asyncio.sleep(0.05)  # Simulate async work
                return f"Summary of {prompt}"

            mock_agent = MagicMock()
            mock_agent.generate_async = delayed_generate

            config = {
                "agent": {
                    "instruction": "Summarize {input}",
                    "tools": [],
                },
                "max_workers": 4,  # Allow parallelism
            }

            with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
                worker = _make_fan_out_worker(config)

                # Create tasks for multiple items
                # Each task will spawn an async worker
                fan_out_results = []

                # Process 5 items in separate invocations (simulating them arriving as separate tasks)
                for i in range(5):
                    task = Task.create("fan_out_output" if i > 0 else "agent_output", {"result": f"file{i}.py"})
                    # Collect all yielded tasks
                    fan_out_results.extend([yielded_task async for yielded_task in worker(task)])

                # Verify we got all 5 results
                self.assertEqual(len(fan_out_results), 5, "Should have 5 fan_out_output tasks")

                # Now send a completion signal
                completion_task = Task.create(
                    "STEP_COMPLETE",
                    {"source": "iterative_discovery", "count": 5},
                    metadata={"is_completion_signal": True},
                )

                completion_results = []
                completion_results = [yielded_task async for yielded_task in worker(completion_task)]

                # Verify we got the completion signal
                self.assertEqual(len(completion_results), 1)
                self.assertEqual(completion_results[0].type, "STEP_COMPLETE")

                # Verify all 5 async calls were made
                self.assertEqual(call_count["count"], 5, "All 5 async workers should have completed")
                print("✓ Fan_out correctly waits for all workers before passing completion signal")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
