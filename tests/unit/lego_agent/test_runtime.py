from unittest.mock import MagicMock, patch

import pytest

from lego_agent.runtime import (
    DEFAULT_AGENT_TIMEOUT,
    RUNNABLE_TYPES,
    FanOut,
    LangGraphAgent,
    _build_runnable,
    fan_out,
    judge_loop,
    summarize,
)


def test_fan_out():
    # spec=['run'] ensures the mock doesn't accidentally pass isinstance(AsyncRunnable)
    agent = MagicMock(spec=["run"])
    # Mock run as it is called by fan_out (via asyncio.to_thread for non-AsyncRunnable)
    agent.run.side_effect = ["resp1", "resp2"]

    results = fan_out(agent, ["p1", "p2"])

    assert results == ["resp1", "resp2"]
    assert agent.run.call_count == 2
    # Verify called with keyword args (or positional)
    call_args_list = agent.run.call_args_list
    assert call_args_list[0].args[0] == "p1"
    assert call_args_list[1].args[0] == "p2"


def test_summarize():
    agent = MagicMock()
    agent.run.return_value = "Summary"

    res = summarize(agent, ["a", "b"], "Summarize this")

    assert res == "Summary"
    call_args = agent.run.call_args
    assert "a\n\n---\n\nb" in call_args.args[0]
    assert "Summarize this" in call_args.args[0]


def test_judge_loop_task_already_done():
    """Test when judge decides task is done before worker starts."""
    worker = MagicMock()
    judge = MagicMock()
    # Judge says done immediately
    judge.run.return_value = '{"status": "done", "feedback": "Task already completed"}'

    result = judge_loop(judge, worker, "task", max_iterations=3)

    assert result["final_output"] == ""
    assert result["judge_feedback"] == "Task already completed"
    assert result["iterations"] == "1"
    worker.run.assert_not_called()


def test_judge_loop_worker_immediate_success():
    """Test when worker succeeds on first attempt (after initial judge check)."""
    worker = MagicMock()
    worker.run.return_value = "Work output"

    judge = MagicMock()
    judge.run.side_effect = [
        # First check (pre-work)
        '{"status": "continue", "feedback": "Please start"}',
        # Second check (post-work)
        '{"status": "done", "feedback": "Good job"}',
    ]

    result = judge_loop(judge, worker, "task", max_iterations=3)

    assert result["final_output"] == "Work output"
    assert result["judge_feedback"] == "Good job"
    assert result["iterations"] == "2"
    assert worker.run.call_count == 1


def test_judge_loop_refinement():
    worker = MagicMock()
    worker.run.side_effect = ["Bad output", "Good output"]

    judge = MagicMock()
    judge.run.side_effect = [
        '{"status": "continue", "feedback": "Start"}',  # 1. Pre-check
        '{"status": "continue", "feedback": "Fix it"}',  # 2. Check bad output
        '{"status": "done", "feedback": "Perfect"}',  # 3. Check good output
    ]

    result = judge_loop(judge, worker, "task", max_iterations=3)

    assert result["final_output"] == "Good output"
    assert result["iterations"] == "3"
    assert worker.run.call_count == 2
    # Check that feedback was passed to worker
    # Worker call 0: Start
    # Worker call 1: Fix it
    refine_call = worker.run.call_args_list[1]
    assert "Fix it" in refine_call.args[0]


# --- Registry pattern tests (issue 5) ---


def test_runnable_types_registry_contains_all_types():
    """RUNNABLE_TYPES should list all supported kinds."""
    expected = {"agent", "chain", "fan_out", "summarize", "judge_loop"}
    assert set(RUNNABLE_TYPES.keys()) == expected


def test_build_runnable_unknown_type():
    """Unknown type should raise ValueError."""
    with pytest.raises(ValueError, match="Unknown Runnable type: bogus"):
        _build_runnable({"type": "bogus"})


def test_build_runnable_chain_requires_steps():
    with pytest.raises(ValueError, match="Chain must have 'steps'"):
        _build_runnable({"type": "chain", "steps": []})


def test_build_runnable_fan_out_requires_agent():
    with pytest.raises(ValueError, match="FanOut must have 'agent'"):
        _build_runnable({"type": "fan_out"})


def test_build_runnable_summarize_requires_agent():
    with pytest.raises(ValueError, match="Summarize must have 'agent'"):
        _build_runnable({"type": "summarize"})


def test_build_runnable_judge_loop_requires_judge():
    with pytest.raises(ValueError, match="JudgeLoop must have 'judge'"):
        _build_runnable({"type": "judge_loop"})


def test_build_runnable_judge_loop_requires_worker():
    with pytest.raises(ValueError, match="JudgeLoop must have 'worker'"):
        _build_runnable({"type": "judge_loop", "judge": {"type": "agent"}})


# --- FanOut timeout configurability (issue 3) ---


# --- {input} placeholder substitution in standalone agent (issue 3) ---


def test_langgraph_agent_run_substitutes_input_placeholder():
    """When instruction contains {input}, run() should substitute input_data into it."""
    agent = LangGraphAgent.__new__(LangGraphAgent)
    agent.instruction = "Analyze the file at path: {input}. Use read_file."

    with patch.object(agent, "generate", return_value="result") as mock_generate:
        agent.run("myfile.py")
        mock_generate.assert_called_once_with("Analyze the file at path: myfile.py. Use read_file.")


def test_langgraph_agent_run_no_placeholder_uses_input_data():
    """When instruction has no {input}, run() should pass input_data as the prompt."""
    agent = LangGraphAgent.__new__(LangGraphAgent)
    agent.instruction = "Do something useful."

    with patch.object(agent, "generate", return_value="result") as mock_generate:
        agent.run("some input")
        mock_generate.assert_called_once_with("some input")


# --- Per-worker output markers for fan_out (issue 2) ---


def test_fan_out_emits_worker_markers(capsys):
    """FanOut should wrap each worker's output in __LEGO_WORKER_START/END__ markers."""

    # Use a real class so isinstance(agent, AsyncRunnable) returns True.
    class FakeAsyncAgent:
        def run(self, input_data):
            return f"result for {input_data}"

        async def generate_async(self, prompt, timeout, output=None):
            if output is not None:
                output.write(f"output for {prompt}")
            return f"result for {prompt}"

    fo = FanOut(FakeAsyncAgent(), ["item0", "item1"])
    results = fo.run("")

    captured = capsys.readouterr().out
    assert "__LEGO_WORKER_START__ 0" in captured
    assert "__LEGO_WORKER_END__ 0" in captured
    assert "__LEGO_WORKER_START__ 1" in captured
    assert "__LEGO_WORKER_END__ 1" in captured
    # Worker 0's output should appear between its markers
    start0 = captured.index("__LEGO_WORKER_START__ 0")
    end0 = captured.index("__LEGO_WORKER_END__ 0")
    assert "output for item0" in captured[start0:end0]
    # Worker 1's output should appear between its markers
    start1 = captured.index("__LEGO_WORKER_START__ 1")
    end1 = captured.index("__LEGO_WORKER_END__ 1")
    assert "output for item1" in captured[start1:end1]
    assert results == ["result for item0", "result for item1"]


def test_fan_out_default_timeout():
    agent = MagicMock()
    fo = FanOut(agent, ["p1"])
    assert fo.timeout == DEFAULT_AGENT_TIMEOUT


def test_fan_out_custom_timeout():
    agent = MagicMock()
    fo = FanOut(agent, ["p1"], timeout=600)
    assert fo.timeout == 600
