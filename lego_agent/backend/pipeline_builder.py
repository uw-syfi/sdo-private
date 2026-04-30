"""YAML-to-Pipeline adapter: translates v1 workflow configs into v2 Pipelines."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from loguru import logger

from .queue_runtime import Pipeline, Stage, Task, run_pipeline
from .runtime import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_FAN_OUT_MAX_WORKERS,
    DEFAULT_JUDGE_LOOP_MAX_ITERATIONS,
    create_agent,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass
class _StageDef:
    name: str
    kind: str
    config: dict[str, Any]


def _collect_stage_defs(config: dict[str, Any], _counter: list[int] | None = None) -> list[_StageDef]:
    """Flatten a YAML workflow config into an ordered list of stage definitions.

    Only `chain` creates multiple stages; all other types become a single stage.
    Nested chains are recursively flattened into the same list.
    """
    if _counter is None:
        _counter = [0]

    kind = config.get("type", "")

    if kind == "chain":
        result: list[_StageDef] = []
        for step in config.get("steps", []):
            result.extend(_collect_stage_defs(step, _counter))
        return result

    i = _counter[0]
    _counter[0] += 1
    return [_StageDef(name=f"{kind}_{i}", kind=kind, config=config)]


def _input_value(task: Task) -> Any:
    """Extract the primary input value from a task, preferring 'result' over 'input'."""
    return task.payload.get("result") or task.payload.get("input") or ""


def _make_agent_worker(
    config: dict[str, Any],
) -> Callable[[Task], Awaitable[list[Task]]]:
    instruction = config.get("instruction", "")
    agent = create_agent(
        instruction=instruction,
        tools=config.get("tools"),
        provider=config.get("provider"),
        model=config.get("model"),
    )

    async def worker_fn(task: Task) -> list[Task]:
        input_val = _input_value(task)
        if instruction and "{input}" in instruction:
            prompt = instruction.replace("{input}", str(input_val))
        else:
            prompt = str(input_val) if input_val else instruction
        result = await agent.generate_async(prompt, timeout=DEFAULT_AGENT_TIMEOUT)
        return [Task.create("agent_output", {"result": result})]

    return worker_fn


def _make_fan_out_worker(
    config: dict[str, Any],
) -> Callable[[Task], Awaitable[list[Task]]]:
    agent_config = config["agent"]
    instruction = agent_config.get("instruction", "")
    static_items: list[str] = config.get("items", [])
    max_workers = config.get("max_workers", DEFAULT_FAN_OUT_MAX_WORKERS)
    timeout = config.get("timeout", DEFAULT_AGENT_TIMEOUT)

    agent = create_agent(
        instruction=instruction,
        tools=agent_config.get("tools"),
        provider=agent_config.get("provider"),
        model=agent_config.get("model"),
    )

    async def worker_fn(task: Task) -> list[Task]:
        input_data = _input_value(task)

        items: list[str] = list(static_items)
        if not items and input_data:
            raw_lines = [line.strip() for line in str(input_data).strip().splitlines() if line.strip()]
            _prefix = re.compile(r"^(\d+[\.\)]\s+|[-*•]\s+)")
            items = [_prefix.sub("", line) for line in raw_lines if not line.endswith(":")]

        print(f"__LEGO_FANOUT_INIT__ {len(items)}", flush=True)

        semaphore = asyncio.Semaphore(max_workers)

        async def run_one(i: int, item: str) -> tuple[int, str]:
            async with semaphore:
                if instruction and "{input}" in instruction:
                    prompt = instruction.replace("{input}", str(item))
                else:
                    prompt = str(item)
                result = await agent.generate_async(prompt, timeout=timeout)
                return i, result

        indexed = await asyncio.gather(*[run_one(i, item) for i, item in enumerate(items)])
        results = [r for _, r in sorted(indexed)]
        return [Task.create("fan_out_output", {"result": results})]

    return worker_fn


def _make_summarize_worker(
    config: dict[str, Any],
) -> Callable[[Task], Awaitable[list[Task]]]:
    agent_config = config["agent"]
    instruction = config.get("instruction", "Summarize the inputs.")

    agent = create_agent(
        instruction=instruction,
        tools=agent_config.get("tools"),
        provider=agent_config.get("provider"),
        model=agent_config.get("model"),
    )

    async def worker_fn(task: Task) -> list[Task]:
        input_data = _input_value(task)
        if isinstance(input_data, list):
            combined = "\n\n---\n\n".join(str(x) for x in input_data)
        else:
            combined = str(input_data)
        prompt = f"{instruction}\n\nHere are the inputs to summarize:\n{combined}"
        result = await agent.generate_async(prompt, timeout=DEFAULT_AGENT_TIMEOUT)
        return [Task.create("summarize_output", {"result": result})]

    return worker_fn


def _parse_judge_feedback(judge_resp: str) -> dict[str, str]:
    try:
        start = judge_resp.find("{")
        end = judge_resp.rfind("}")
        if start != -1 and end != -1:
            return json.loads(judge_resp[start : end + 1])
    except Exception:
        pass
    return {
        "status": "continue",
        "feedback": f"Judge did not return JSON. Raw: {judge_resp}",
    }


def _make_judge_loop_worker(
    config: dict[str, Any],
) -> Callable[[Task], Awaitable[list[Task]]]:
    judge_config = config["judge"]
    worker_config = config["worker"]
    task_desc = config.get("task", "")
    max_iterations = config.get("max_iterations", DEFAULT_JUDGE_LOOP_MAX_ITERATIONS)

    judge_agent = create_agent(
        instruction=judge_config.get("instruction", ""),
        tools=judge_config.get("tools"),
        provider=judge_config.get("provider"),
        model=judge_config.get("model"),
    )
    worker_agent = create_agent(
        instruction=worker_config.get("instruction", ""),
        tools=worker_config.get("tools"),
        provider=worker_config.get("provider"),
        model=worker_config.get("model"),
    )

    async def worker_fn(task: Task) -> list[Task]:
        current_output: str | None = None

        for i in range(max_iterations):
            logger.info(f"Judge loop iteration {i + 1}/{max_iterations}")
            current_output_line = (
                "Current Output: (None - Worker has not started yet)"
                if current_output is None
                else f"Current Output:\n{current_output}"
            )
            judge_prompt = (
                f"Task: {task_desc}\n\n"
                f"{current_output_line}\n\n"
                "=== YOUR ROLE: JUDGE/EVALUATOR ===\n"
                "You are an evaluator who assesses whether the task is complete. "
                "You make decisions but DO NOT perform work.\n\n"
                "DO:\n"
                "- Evaluate if the task requirements are met\n"
                "- Provide specific, actionable feedback if work is needed\n"
                "- Mark as 'done' only when task is FULLY satisfied\n"
                '- Respond with strictly JSON: {"status": "continue" or "done", "feedback": "..."}\n'
            )
            judge_resp = await judge_agent.generate_async(judge_prompt, timeout=DEFAULT_AGENT_TIMEOUT)
            feedback = _parse_judge_feedback(judge_resp)
            logger.info(f"Judge feedback: {feedback}")

            if feedback.get("status") == "done":
                return [
                    Task.create(
                        "judge_loop_output",
                        {
                            "result": {
                                "final_output": current_output or "",
                                "judge_feedback": feedback.get("feedback", ""),
                                "iterations": str(i + 1),
                            }
                        },
                    )
                ]

            worker_prompt = (
                f"Task: {task_desc}\n\n"
                f"Previous Output:\n{current_output}\n\n"
                f"Feedback:\n{feedback.get('feedback')}\n\n"
                "=== YOUR ROLE: WORKER/IMPLEMENTER ===\n"
                "You are responsible for executing the task/refinements based on feedback.\n"
                "Please perform the task now."
            )
            current_output = await worker_agent.generate_async(worker_prompt, timeout=DEFAULT_AGENT_TIMEOUT)

        return [
            Task.create(
                "judge_loop_output",
                {
                    "result": {
                        "final_output": current_output or "",
                        "judge_feedback": "Max iterations reached",
                        "iterations": str(max_iterations),
                    }
                },
            )
        ]

    return worker_fn


_WORKER_FACTORIES: dict[str, Callable[[dict[str, Any]], Callable[[Task], Awaitable[list[Task]]]]] = {
    "agent": _make_agent_worker,
    "fan_out": _make_fan_out_worker,
    "summarize": _make_summarize_worker,
    "judge_loop": _make_judge_loop_worker,
}

# These types manage their own internal concurrency; stage-level max_workers must
# be 1 to prevent multiple tasks from racing on the same queue accounting.
_SINGLE_WORKER_KINDS = frozenset({"fan_out", "summarize", "judge_loop"})


def build_pipeline_from_yaml(workflow_config: dict[str, Any]) -> Pipeline:
    """Translate a YAML workflow config dict into a v2 Pipeline.

    Chains are flattened into sequential stages; all other types become
    a single stage. Queue names are assigned as q0, q1, ... qN-1.
    """
    stage_defs = _collect_stage_defs(workflow_config)
    if not stage_defs:
        raise ValueError("Workflow config produced no stages.")

    stages: list[Stage] = []
    for i, sdef in enumerate(stage_defs):
        factory = _WORKER_FACTORIES.get(sdef.kind)
        if factory is None:
            raise ValueError(f"Unknown workflow type: '{sdef.kind}'")

        input_queue = f"q{i}"
        output_queue = f"q{i + 1}" if i < len(stage_defs) - 1 else None
        max_workers = 1 if sdef.kind in _SINGLE_WORKER_KINDS else sdef.config.get("max_workers", 1)

        stages.append(
            Stage(
                name=sdef.name,
                input_queue=input_queue,
                output_queue=output_queue,
                worker_fn=factory(sdef.config),
                max_workers=max_workers,
            )
        )

    return Pipeline(stages=stages)


async def _run_yaml_v2_async(config_path: str) -> None:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path) as f:
        config = yaml.safe_load(f)

    workflow_config = config.get("workflow")
    if not workflow_config:
        raise ValueError("YAML must contain a 'workflow' root object.")

    pipeline = build_pipeline_from_yaml(workflow_config)
    await run_pipeline(pipeline, [Task.create("start", {"input": None})])


def run_yaml_v2(config_path: str) -> None:
    """Entry point to execute a YAML config via the v2 queue-based runtime."""
    asyncio.run(_run_yaml_v2_async(config_path))
