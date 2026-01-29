from unittest.mock import MagicMock
from agentflow.runtime import fan_out, summarize, judge_loop


def test_fan_out():
    agent = MagicMock()
    # Mock return values for parallel calls
    agent.generate.side_effect = ["resp1", "resp2"]

    results = fan_out(agent, ["p1", "p2"])

    assert results == ["resp1", "resp2"]
    assert agent.generate.call_count == 2
    # Verify called with keyword args
    call_args_list = agent.generate.call_args_list
    assert call_args_list[0].kwargs["prompt"] == "p1"
    assert call_args_list[1].kwargs["prompt"] == "p2"


def test_summarize():
    agent = MagicMock()
    agent.generate.return_value = "Summary"

    res = summarize(agent, ["a", "b"], "Summarize this")

    assert res == "Summary"
    call_args = agent.generate.call_args
    assert "a\n\n---\n\nb" in call_args.kwargs["prompt"]
    assert "Summarize this" in call_args.kwargs["prompt"]


def test_judge_loop_immediate_success():
    worker = MagicMock()
    worker.generate.return_value = "Work output"

    judge = MagicMock()
    judge.generate.return_value = '{"status": "done", "feedback": "Good job"}'

    result = judge_loop(judge, worker, "task", max_iterations=3)

    assert result["final_output"] == "Work output"
    assert result["judge_feedback"] == "Good job"
    assert result["iterations"] == "1"


def test_judge_loop_refinement():
    worker = MagicMock()
    worker.generate.side_effect = ["Bad output", "Good output"]

    judge = MagicMock()
    judge.generate.side_effect = [
        '{"status": "continue", "feedback": "Fix it"}',
        '{"status": "done", "feedback": "Perfect"}',
    ]

    result = judge_loop(judge, worker, "task", max_iterations=3)

    assert result["final_output"] == "Good output"
    assert result["iterations"] == "2"
    assert worker.generate.call_count == 2
    # Check that feedback was passed to worker
    refine_call = worker.generate.call_args_list[1]
    assert "Fix it" in refine_call.kwargs["prompt"]
