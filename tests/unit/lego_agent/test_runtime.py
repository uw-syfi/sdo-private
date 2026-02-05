from unittest.mock import MagicMock, AsyncMock
from lego_agent.runtime import fan_out, summarize, judge_loop


def test_fan_out():
    agent = MagicMock()
    # Mock _generate_async as it is called by fan_out
    agent._generate_async = AsyncMock(side_effect=["resp1", "resp2"])

    # We also mock generate just in case, but fan_out calls _generate_async
    agent.generate.side_effect = ["resp1", "resp2"]

    results = fan_out(agent, ["p1", "p2"])

    assert results == ["resp1", "resp2"]
    assert agent._generate_async.call_count == 2
    # Verify called with keyword args (or positional)
    call_args_list = agent._generate_async.call_args_list
    assert call_args_list[0].args[0] == "p1"
    assert call_args_list[1].args[0] == "p2"


def test_summarize():
    agent = MagicMock()
    agent.generate.return_value = "Summary"

    res = summarize(agent, ["a", "b"], "Summarize this")

    assert res == "Summary"
    call_args = agent.generate.call_args
    assert "a\n\n---\n\nb" in call_args.kwargs["prompt"]
    assert "Summarize this" in call_args.kwargs["prompt"]


def test_judge_loop_task_already_done():
    """Test when judge decides task is done before worker starts."""
    worker = MagicMock()
    judge = MagicMock()
    # Judge says done immediately
    judge.generate.return_value = '{"status": "done", "feedback": "Task already completed"}'

    result = judge_loop(judge, worker, "task", max_iterations=3)

    assert result["final_output"] == ""
    assert result["judge_feedback"] == "Task already completed"
    assert result["iterations"] == "1"
    worker.generate.assert_not_called()


def test_judge_loop_worker_immediate_success():
    """Test when worker succeeds on first attempt (after initial judge check)."""
    worker = MagicMock()
    worker.generate.return_value = "Work output"

    judge = MagicMock()
    judge.generate.side_effect = [
        '{"status": "continue", "feedback": "Please start"}',  # First check (pre-work)
        '{"status": "done", "feedback": "Good job"}'        # Second check (post-work)
    ]

    result = judge_loop(judge, worker, "task", max_iterations=3)

    assert result["final_output"] == "Work output"
    assert result["judge_feedback"] == "Good job"
    assert result["iterations"] == "2"
    assert worker.generate.call_count == 1


def test_judge_loop_refinement():
    worker = MagicMock()
    worker.generate.side_effect = ["Bad output", "Good output"]

    judge = MagicMock()
    judge.generate.side_effect = [
        '{"status": "continue", "feedback": "Start"}',      # 1. Pre-check
        '{"status": "continue", "feedback": "Fix it"}',     # 2. Check bad output
        '{"status": "done", "feedback": "Perfect"}',        # 3. Check good output
    ]

    result = judge_loop(judge, worker, "task", max_iterations=3)

    assert result["final_output"] == "Good output"
    assert result["iterations"] == "3"
    assert worker.generate.call_count == 2
    # Check that feedback was passed to worker
    # Worker call 0: Start
    # Worker call 1: Fix it
    refine_call = worker.generate.call_args_list[1]
    assert "Fix it" in refine_call.kwargs["prompt"]
