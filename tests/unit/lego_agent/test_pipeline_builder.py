from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

from lego_agent.backend.pipeline_builder import (
    _collect_stage_defs,
    _parse_judge_feedback,
    _run_yaml_v2_async,
    build_pipeline_from_yaml,
)
from lego_agent.backend.queue_runtime import Task


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.generate_async = AsyncMock(return_value="mock_llm_output")
    return agent


# ---------------------------------------------------------------------------
# _collect_stage_defs
# ---------------------------------------------------------------------------


def test_collect_agent_yields_one_stage():
    defs = _collect_stage_defs({"type": "agent", "instruction": "Do X"})
    assert len(defs) == 1
    assert defs[0].kind == "agent"
    assert defs[0].name == "agent_0"


def test_collect_chain_flattens_steps():
    config = {
        "type": "chain",
        "steps": [
            {"type": "agent", "instruction": "A"},
            {"type": "agent", "instruction": "B"},
            {"type": "agent", "instruction": "C"},
        ],
    }
    defs = _collect_stage_defs(config)
    assert [d.kind for d in defs] == ["agent", "agent", "agent"]
    assert [d.name for d in defs] == ["agent_0", "agent_1", "agent_2"]


def test_collect_nested_chain_flattens():
    config = {
        "type": "chain",
        "steps": [
            {"type": "agent", "instruction": "A"},
            {
                "type": "chain",
                "steps": [
                    {"type": "fan_out", "agent": {"type": "agent", "instruction": "B"}},
                    {"type": "summarize", "agent": {"type": "agent", "instruction": "C"}},
                ],
            },
        ],
    }
    defs = _collect_stage_defs(config)
    assert [d.kind for d in defs] == ["agent", "fan_out", "summarize"]


def test_collect_fan_out_yields_one_stage():
    config = {"type": "fan_out", "items": [], "agent": {"type": "agent", "instruction": "B"}}
    defs = _collect_stage_defs(config)
    assert len(defs) == 1
    assert defs[0].kind == "fan_out"


def test_collect_judge_loop_yields_one_stage():
    config = {
        "type": "judge_loop",
        "task": "Write code",
        "judge": {"type": "agent", "instruction": "Evaluate"},
        "worker": {"type": "agent", "instruction": "Execute"},
    }
    defs = _collect_stage_defs(config)
    assert len(defs) == 1
    assert defs[0].kind == "judge_loop"


# ---------------------------------------------------------------------------
# build_pipeline_from_yaml — structure
# ---------------------------------------------------------------------------


def test_single_agent_has_no_output_queue(mock_agent):
    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml({"type": "agent", "instruction": "Do X"})

    assert len(pipeline.stages) == 1
    stage = pipeline.stages[0]
    assert stage.input_queue == "q0"
    assert stage.output_queue is None
    assert stage.max_workers == 1


def test_chain_wires_queues_sequentially(mock_agent):
    config = {
        "type": "chain",
        "steps": [
            {"type": "agent", "instruction": "A"},
            {"type": "agent", "instruction": "B"},
            {"type": "agent", "instruction": "C"},
        ],
    }
    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    assert len(pipeline.stages) == 3
    assert pipeline.stages[0].input_queue == "q0"
    assert pipeline.stages[0].output_queue == "q1"
    assert pipeline.stages[1].input_queue == "q1"
    assert pipeline.stages[1].output_queue == "q2"
    assert pipeline.stages[2].input_queue == "q2"
    assert pipeline.stages[2].output_queue is None


def test_fan_out_stage_has_single_worker(mock_agent):
    config = {
        "type": "fan_out",
        "items": ["a", "b"],
        "max_workers": 8,
        "agent": {"type": "agent", "instruction": "X"},
    }
    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    assert pipeline.stages[0].max_workers == 1


def test_judge_loop_stage_has_single_worker(mock_agent):
    config = {
        "type": "judge_loop",
        "task": "Write code",
        "judge": {"type": "agent", "instruction": "Evaluate"},
        "worker": {"type": "agent", "instruction": "Execute"},
    }
    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    assert pipeline.stages[0].max_workers == 1


def test_unknown_type_raises(mock_agent):
    with pytest.raises(ValueError, match="Unknown workflow type"):
        build_pipeline_from_yaml({"type": "banana"})


# ---------------------------------------------------------------------------
# Worker functions
# ---------------------------------------------------------------------------


async def test_agent_worker_substitutes_input_placeholder(mock_agent):
    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml({"type": "agent", "instruction": "Process {input}"})

    task = Task.create("start", {"input": "hello"})
    result_tasks = await pipeline.stages[0].worker_fn(task)

    assert result_tasks[0].payload["result"] == "mock_llm_output"
    prompt = mock_agent.generate_async.call_args.args[0]
    assert "hello" in prompt
    assert "{input}" not in prompt


async def test_agent_worker_reads_result_from_prior_stage(mock_agent):
    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml({"type": "agent", "instruction": "Do X"})

    task = Task.create("start", {"result": "prior_output"})
    result_tasks = await pipeline.stages[0].worker_fn(task)

    assert result_tasks[0].payload["result"] == "mock_llm_output"


async def test_iterative_discovery_stops_on_done_line_with_extra_text(mock_agent):
    config = {
        "type": "agent",
        "instruction": "Discover files",
        "emit_mode": "iterative_discovery",
        "batch_size": 10,
    }
    mock_agent.generate_async = AsyncMock(return_value="file1.md\nDONE: no more files")

    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    emitted: list[str] = []
    task = Task.create("start", {"input": "repo root"})
    emitted = [
        out_task.payload["result"]
        async for out_task in pipeline.stages[0].worker_fn(task)
        if out_task.type != "STEP_COMPLETE"
    ]

    assert emitted == ["file1.md"]
    assert mock_agent.generate_async.call_count == 1


async def test_iterative_discovery_stops_after_consecutive_duplicate_batches(mock_agent):
    config = {
        "type": "agent",
        "instruction": "Discover files",
        "emit_mode": "iterative_discovery",
        "batch_size": 2,
    }
    mock_agent.generate_async = AsyncMock(
        side_effect=[
            "file1.md\nfile2.md",
            "file1.md\nfile2.md",
            "file1.md\nfile2.md",
        ]
    )

    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    emitted: list[str] = []
    task = Task.create("start", {"input": "repo root"})
    emitted = [
        out_task.payload["result"]
        async for out_task in pipeline.stages[0].worker_fn(task)
        if out_task.type != "STEP_COMPLETE"
    ]

    assert emitted == ["file1.md", "file2.md"]
    assert mock_agent.generate_async.call_count == 3


async def test_iterative_discovery_stops_on_model_error_response(mock_agent):
    config = {
        "type": "agent",
        "instruction": "Discover files",
        "emit_mode": "iterative_discovery",
        "batch_size": 5,
    }
    mock_agent.generate_async = AsyncMock(
        return_value=("Error: Error calling model 'gemini-2.5-flash' (Too Many Requests): 429 Too Many Requests")
    )

    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    emitted: list[str] = []
    task = Task.create("start", {"input": "repo root"})
    emitted = [
        out_task.payload["result"]
        async for out_task in pipeline.stages[0].worker_fn(task)
        if out_task.type != "STEP_COMPLETE"
    ]

    assert emitted == []
    assert mock_agent.generate_async.call_count == 1


async def test_fan_out_worker_static_items(mock_agent):
    config = {
        "type": "fan_out",
        "items": ["item1", "item2"],
        "agent": {"type": "agent", "instruction": "Process {input}"},
    }
    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    result_tasks = [task async for task in pipeline.stages[0].worker_fn(Task.create("start", {"input": None}))]

    assert mock_agent.generate_async.call_count == 2
    # Collect results from fan_out_output tasks (skip any completion signals)
    outputs = [t.payload["result"] for t in result_tasks if t.type == "fan_out_output"]
    assert outputs == ["mock_llm_output", "mock_llm_output"]


async def test_fan_out_worker_dynamic_items_from_previous_output(mock_agent):
    config = {
        "type": "fan_out",
        "items": [],
        "agent": {"type": "agent", "instruction": "Process {input}"},
    }
    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    task = Task.create("start", {"result": "file1.txt\nfile2.txt\nfile3.txt"})
    result_tasks = [t async for t in pipeline.stages[0].worker_fn(task)]

    assert mock_agent.generate_async.call_count == 3
    # Collect results from fan_out_output tasks (skip any completion signals)
    outputs = [t.payload["result"] for t in result_tasks if t.type == "fan_out_output"]
    assert outputs == ["mock_llm_output"] * 3


async def test_summarize_worker_joins_list_input(mock_agent):
    config = {
        "type": "summarize",
        "instruction": "Summarize these:",
        "agent": {"type": "agent", "instruction": ""},
    }
    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    task = Task.create("start", {"result": ["res1", "res2"]})
    result_tasks = await pipeline.stages[0].worker_fn(task)

    assert result_tasks[0].payload["result"] == "mock_llm_output"
    prompt = mock_agent.generate_async.call_args.args[0]
    assert "res1" in prompt
    assert "res2" in prompt


async def test_fan_out_then_summarize_aggregates_all_items_once():
    fan_out_agent = MagicMock()
    fan_out_agent.generate_async = AsyncMock(side_effect=["processed_a", "processed_b", "processed_c"])

    summarize_agent = MagicMock()
    summarize_agent.generate_async = AsyncMock(return_value="final_summary")

    config = {
        "type": "chain",
        "steps": [
            {
                "type": "fan_out",
                "items": ["a.py", "b.py", "c.py"],
                "agent": {"type": "agent", "instruction": "Process {input}"},
            },
            {
                "type": "summarize",
                "instruction": "Summarize all items",
                "agent": {"type": "agent", "instruction": "You summarize"},
            },
        ],
    }

    with patch(
        "lego_agent.backend.pipeline_builder.create_agent",
        side_effect=[fan_out_agent, summarize_agent],
    ):
        pipeline = build_pipeline_from_yaml(config)

    from lego_agent.backend.queue_runtime import run_pipeline

    await run_pipeline(pipeline, [Task.create("start", {"input": None})], max_concurrent_workers=2)

    assert fan_out_agent.generate_async.call_count == 3
    assert summarize_agent.generate_async.call_count == 1
    summary_prompt = summarize_agent.generate_async.call_args.args[0]
    assert "processed_a" in summary_prompt
    assert "processed_b" in summary_prompt
    assert "processed_c" in summary_prompt


async def test_judge_loop_worker_returns_done_on_first_done_signal(mock_agent):
    config = {
        "type": "judge_loop",
        "task": "Write tests",
        "judge": {"type": "agent", "instruction": "Evaluate"},
        "worker": {"type": "agent", "instruction": "Execute"},
        "max_iterations": 5,
    }
    mock_agent.generate_async.return_value = '{"status": "done", "feedback": "great"}'

    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    result_tasks = await pipeline.stages[0].worker_fn(Task.create("start", {}))

    assert len(result_tasks) == 1
    result = result_tasks[0].payload["result"]
    assert result["judge_feedback"] == "great"
    assert result["iterations"] == "1"


async def test_judge_loop_worker_hits_max_iterations(mock_agent):
    config = {
        "type": "judge_loop",
        "task": "Write tests",
        "judge": {"type": "agent", "instruction": "Evaluate"},
        "worker": {"type": "agent", "instruction": "Execute"},
        "max_iterations": 2,
    }
    mock_agent.generate_async.return_value = '{"status": "continue", "feedback": "keep going"}'

    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        pipeline = build_pipeline_from_yaml(config)

    result_tasks = await pipeline.stages[0].worker_fn(Task.create("start", {}))

    result = result_tasks[0].payload["result"]
    assert result["judge_feedback"] == "Max iterations reached"
    assert result["iterations"] == "2"


# ---------------------------------------------------------------------------
# _parse_judge_feedback
# ---------------------------------------------------------------------------


def test_parse_valid_json():
    resp = 'Some text {"status": "done", "feedback": "looks good"} extra'
    assert _parse_judge_feedback(resp) == {"status": "done", "feedback": "looks good"}


def test_parse_no_json_returns_continue():
    result = _parse_judge_feedback("not json at all")
    assert result["status"] == "continue"
    assert "Judge did not return JSON" in result["feedback"]


# ---------------------------------------------------------------------------
# _run_yaml_v2_async — integration
# ---------------------------------------------------------------------------


async def test_run_yaml_v2_async_runs_pipeline(mock_agent, tmp_path: Path):
    config_file = tmp_path / "workflow.yaml"
    config_file.write_text(yaml.dump({"workflow": {"type": "agent", "instruction": "Do something"}}))

    with patch("lego_agent.backend.pipeline_builder.create_agent", return_value=mock_agent):
        await _run_yaml_v2_async(str(config_file))

    mock_agent.generate_async.assert_called_once()


async def test_run_yaml_v2_async_missing_file():
    with pytest.raises(FileNotFoundError):
        await _run_yaml_v2_async("/nonexistent/path.yaml")


async def test_run_yaml_v2_async_missing_workflow_key(tmp_path: Path):
    config_file = tmp_path / "bad.yaml"
    config_file.write_text("not_workflow: {}")

    with pytest.raises(ValueError, match="'workflow'"):
        await _run_yaml_v2_async(str(config_file))
