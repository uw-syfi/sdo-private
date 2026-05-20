"""YAML-to-Pipeline adapter: translates v1 workflow configs into v2 Pipelines."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from loguru import logger

from .queue_runtime import Pipeline, Stage, Task, run_pipeline
from .runtime import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_FAN_OUT_MAX_WORKERS,
    DEFAULT_JUDGE_LOOP_MAX_ITERATIONS,
    create_agent,
)
from .storage import LegoAgentStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


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
    logger_fn: Callable[[str, dict[str, Any]], None] | None = None,
    stage_name: str | None = None,
) -> Callable[[Task], Any]:
    """Create an agent worker with optional streaming discovery.

    By default, agent runs once and returns a single Task with the full output.

    With emit_mode="iterative_discovery", the agent makes multiple LLM calls to
    discover items one batch at a time. Each discovery call yields results immediately,
    allowing downstream workers to start processing before all items are discovered.
    This is critical for discovering large numbers of items (100s-1000s) without
    waiting for the final result to be assembled.

    Config options:
    - instruction: str (required) - the prompt to send to the agent
    - tools: list[str] - tools the agent can use
    - provider: str - LLM provider
    - model: str - LLM model name
    - emit_mode: "batch" (default) or "iterative_discovery"
      * "batch": single LLM call, return result as one Task (traditional, slower for discovery)
      * "iterative_discovery": multiple LLM calls to find batches, yield per-batch (streaming)
    - batch_size: int (default: 25, for iterative_discovery mode) - items per batch

    For iterative_discovery mode:
    - The instruction should ask the agent to find items in batches
    - Agent should output items one per line
    - Agent should say "DONE" when no more items
    """
    instruction = config.get("instruction", "")
    emit_mode = config.get("emit_mode", "batch")
    batch_size = config.get("batch_size", 25)  # Configurable batch size for discovery

    agent = create_agent(
        instruction=instruction,
        tools=config.get("tools"),
        provider=config.get("provider"),
        model=config.get("model"),
        logger_fn=logger_fn,
    )

    async def batch_worker(task: Task) -> list[Task]:
        """Traditional mode: single agent call, return all results."""
        input_val = _input_value(task)
        if instruction and "{input}" in instruction:
            prompt = instruction.replace("{input}", str(input_val))
        else:
            prompt = str(input_val) if input_val else instruction

        result = await agent.generate_async(prompt, timeout=DEFAULT_AGENT_TIMEOUT, agent_id=stage_name)
        return [Task.create("agent_output", {"result": result})]

    async def iterative_discovery_worker(task: Task) -> AsyncIterator[Task]:
        """Streaming discovery mode: iterative LLM calls, yield per batch."""
        input_val = _input_value(task)
        if instruction and "{input}" in instruction:
            base_instruction = instruction.replace("{input}", str(input_val))
        else:
            base_instruction = instruction

        discovered_items: set[str] = set()
        done_markers = ("done", "no more", "none found", "finished")
        consecutive_no_new_batches = 0
        batch_num = 0
        max_iterations = 100  # Safety limit

        def _is_done_line(line: str) -> bool:
            normalized = line.strip().lower()
            for marker in done_markers:
                if normalized == marker:
                    return True
                if normalized.startswith(f"{marker}:"):
                    return True
                if normalized.startswith(f"{marker} "):
                    return True
            return False

        while batch_num < max_iterations:
            batch_num += 1

            # Build discovery prompt for this iteration
            if batch_num == 1:
                discovery_prompt = (
                    f"{base_instruction}\n\n"
                    f"Find the FIRST batch of items (up to {batch_size}). "
                    "Output item paths/names only, one per line. "
                    "If no items found, say DONE."
                )
            else:
                already_found = ", ".join(sorted(discovered_items)[:10])  # Show first 10 for context
                discovery_prompt = (
                    f"{base_instruction}\n\n"
                    f"Already found: {already_found}...\n"
                    f"Find the NEXT batch of items (up to {batch_size}) NOT already found. "
                    "Output only the new items, one per line. "
                    "If no more items, say DONE."
                )

            # Make LLM call to discover next batch
            response = await agent.generate_async(discovery_prompt, timeout=DEFAULT_AGENT_TIMEOUT, agent_id=stage_name)
            response_stripped = response.strip()

            logger.info(f"Discovery batch {batch_num}: {len(response_stripped)} chars")

            if response_stripped.startswith("Error:"):
                logger.warning(
                    f"Iterative discovery stopped on model error at batch {batch_num}: {response_stripped[:160]}"
                )
                break

            if not response_stripped:
                logger.info(f"Iterative discovery stopped (empty response) after {batch_num - 1} batches")
                break

            # Parse batch output, allow DONE marker to appear on its own line.
            batch_items = [line.strip() for line in response_stripped.split("\n") if line.strip()]

            saw_done_signal = any(_is_done_line(line) for line in batch_items)
            batch_items = [line for line in batch_items if not _is_done_line(line)]

            new_items = [item for item in batch_items if item not in discovered_items]

            if not new_items:
                consecutive_no_new_batches += 1
            else:
                consecutive_no_new_batches = 0

            for item in new_items:
                discovered_items.add(item)
                yield Task.create("agent_output", {"result": item, "batch": batch_num})
                print(f"__LEGO_AGENT_DISCOVERY__ Batch {batch_num}: {item}", flush=True)

            if saw_done_signal:
                logger.info(f"Iterative discovery complete after {batch_num} batches")
                break

            if consecutive_no_new_batches >= 2:
                logger.info("Iterative discovery stopped after two consecutive batches with no new items")
                break

        if batch_num >= max_iterations:
            logger.warning(f"Iterative discovery hit max iterations ({max_iterations})")

        # Emit completion sentinel to signal downstream stages that discovery is complete
        logger.info(f"Iterative discovery emitting completion sentinel (found {len(discovered_items)} items total)")
        yield Task.create(
            "STEP_COMPLETE",
            {"source": "iterative_discovery", "count": len(discovered_items)},
            metadata={"is_completion_signal": True},
        )
        print(f"__LEGO_DISCOVERY_COMPLETE__ {len(discovered_items)} items", flush=True)

    # Return appropriate worker based on emit_mode
    if emit_mode == "batch":
        return batch_worker
    if emit_mode == "iterative_discovery":
        return iterative_discovery_worker
    raise ValueError(f"Unknown emit_mode: {emit_mode}")


def _make_fan_out_worker(
    config: dict[str, Any],
    logger_fn: Callable[[str, dict[str, Any]], None] | None = None,
    stage_name: str | None = None,
) -> Callable[[Task], Any]:
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
        logger_fn=logger_fn,
    )

    # Closure state: track all pending async tasks and completion signals.
    pending_tasks: set[str] = set()
    completion_lock = asyncio.Lock()
    task_counter = 0

    async def worker_fn(task: Task):
        """Async-generator worker: yields a Task for each fan-out item as it completes.

        This allows the runtime to enqueue downstream tasks incrementally instead of
        waiting for all items to finish. When a completion signal arrives from upstream,
        fan_out buffers it and only passes it through AFTER all async workers have
        completed and yielded their results.
        """
        nonlocal task_counter

        # Recognize and buffer completion sentinels from upstream stages
        if task.type == "STEP_COMPLETE":
            logger.info(
                f"Fan_out received completion signal from {task.payload.get('source')}, "
                f"waiting for {len(pending_tasks)} pending workers to complete"
            )
            # Wait for all pending workers to finish
            while pending_tasks:
                logger.info(f"Waiting for {len(pending_tasks)} workers to complete...")
                await asyncio.sleep(0.1)

            # All workers are done, now pass through the completion signal
            logger.info("All workers complete, passing through completion signal")
            yield task
            return

        input_data = _input_value(task)

        items: list[str] = list(static_items)
        if not items and input_data:
            raw_lines = [line.strip() for line in str(input_data).strip().splitlines() if line.strip()]
            _prefix = re.compile(r"^(\d+[\.\)]\s+|[-*•]\s+)")
            items = [_prefix.sub("", line) for line in raw_lines if not line.endswith(":")]

        print(json.dumps({"type": "log", "message": f"FanOut: {len(items)} workers starting", "level": "info"}), flush=True)

        semaphore = asyncio.Semaphore(max_workers)
        result_q: asyncio.Queue[tuple[int, str]] = asyncio.Queue()

        async def run_one(i: int, item: str, task_id: str) -> None:
            try:
                async with semaphore:
                    if instruction and "{input}" in instruction:
                        prompt = instruction.replace("{input}", str(item))
                    else:
                        prompt = str(item)
                    try:
                        result = await agent.generate_async(prompt, timeout=timeout, agent_id=f"worker_{i}")
                    except Exception as e:
                        logger.error(f"Fan-out worker {i} failed: {e}")
                        result = f"Error: {e!s}"
                    await result_q.put((i, result))
            finally:
                # Mark this task as done
                async with completion_lock:
                    pending_tasks.discard(task_id)

        # Start worker coroutines for all items; semaphore will bound concurrency.
        for i, item in enumerate(items):
            async with completion_lock:
                task_counter += 1
                task_id = f"worker_{task_counter}"
                pending_tasks.add(task_id)
            asyncio.create_task(run_one(i, item, task_id))

        # As results arrive, yield downstream tasks so they get enqueued immediately.
        remaining = len(items)
        while remaining > 0:
            try:
                i, res = await asyncio.wait_for(result_q.get(), timeout=timeout + 10)
            except asyncio.TimeoutError:
                logger.error("Fan-out timeout waiting for result from worker, skipping")
                remaining -= 1
                continue
            is_last = remaining == 1
            yield Task.create(
                "fan_out_output",
                {"result": res},
                metadata={"fan_out_last": is_last, "fan_out_index": i},
            )
            remaining -= 1

        # Emit completion signal so downstream stages know fan_out is done
        logger.info("Fan_out finished processing all items, emitting completion signal")
        yield Task.create(
            "STEP_COMPLETE", {"source": "fan_out", "count": len(items)}, metadata={"is_completion_signal": True}
        )

    return worker_fn


def _make_summarize_worker(
    config: dict[str, Any],
    logger_fn: Callable[[str, dict[str, Any]], None] | None = None,
    stage_name: str | None = None,
) -> Callable[[Task], Any]:
    agent_config = config["agent"]
    instruction = config.get("instruction", "Summarize the inputs.")

    agent = create_agent(
        instruction=instruction,
        tools=agent_config.get("tools"),
        provider=agent_config.get("provider"),
        model=agent_config.get("model"),
        logger_fn=logger_fn,
    )
    buffered_inputs: list[Any] = []
    processing_state: dict[str, Any] = {"count": 0, "done": False}

    async def worker_fn(task: Task) -> list[Task]:
        input_data = _input_value(task)
        processing_state["count"] += 1

        # Check if this is a completion signal from upstream
        if task.type == "STEP_COMPLETE":
            logger.info(f"Summarize received completion signal after buffering {len(buffered_inputs)} items")
            # Process all buffered items and then the completion signal
            if len(buffered_inputs) == 0:
                logger.warning("Summarize received completion signal but no items were buffered")
                return []

            items = buffered_inputs.copy()
            buffered_inputs.clear()
            combined = "\n\n---\n\n".join(str(x) for x in items)

            prompt = f"{instruction}\n\nHere are the inputs to summarize:\n{combined}"
            result = await agent.generate_async(prompt, timeout=DEFAULT_AGENT_TIMEOUT, agent_id=stage_name)
            processing_state["completed"] = True
            logger.info(f"Summarize completed with {len(items)} items after receiving completion signal")
            return [Task.create("summarize_output", {"result": result})]

        # When summarize follows a streaming fan_out, buffer each item until completion signal
        if task.type == "fan_out_output":
            if processing_state.get("completed", False):
                logger.warning(
                    "Summarize received fan_out_output after completion signal, "
                    f"ignoring task #{processing_state['count']}"
                )
                return []

            logger.info(
                f"Summarize buffering fan_out item #{processing_state['count']}, "
                f"buffered_count={len(buffered_inputs) + 1}"
            )
            buffered_inputs.append(input_data)
            # Don't process yet - wait for completion signal
            return []

        if isinstance(input_data, list):
            # Direct list input (not from fan_out streaming)
            items = cast("list[Any]", input_data)
            logger.info(f"Summarize received list input with {len(items)} items")
            combined = "\n\n---\n\n".join(str(x) for x in items)
        else:
            # Single string input
            logger.info("Summarize received single string input")
            combined = str(input_data)

        prompt = f"{instruction}\n\nHere are the inputs to summarize:\n{combined}"
        result = await agent.generate_async(prompt, timeout=DEFAULT_AGENT_TIMEOUT, agent_id=stage_name)
        processing_state["done"] = True
        logger.info(f"Summarize completed after {processing_state['count']} invocations")
        return [Task.create("summarize_output", {"result": result})]

    return worker_fn


def _parse_judge_feedback(judge_resp: str) -> dict[str, str]:
    try:
        start = judge_resp.find("{")
        end = judge_resp.rfind("}")
        if start != -1 and end != -1:
            return json.loads(judge_resp[start : end + 1])
    except Exception:
        logger.exception("Failed to parse judge feedback JSON")
    # Return a continue status when parsing fails
    return {"status": "continue", "feedback": "Judge did not return JSON. Raw: " + judge_resp}


def _make_judge_loop_worker(
    config: dict[str, Any],
    logger_fn: Callable[[str, dict[str, Any]], None] | None = None,
    stage_name: str | None = None,
) -> Callable[[Task], Any]:
    judge_config = config["judge"]
    worker_config = config["worker"]
    task_desc = config.get("task", "")
    max_iterations = config.get("max_iterations", DEFAULT_JUDGE_LOOP_MAX_ITERATIONS)

    judge_agent = create_agent(
        instruction=judge_config.get("instruction", ""),
        tools=judge_config.get("tools"),
        provider=judge_config.get("provider"),
        model=judge_config.get("model"),
        logger_fn=logger_fn,
    )
    worker_agent = create_agent(
        instruction=worker_config.get("instruction", ""),
        tools=worker_config.get("tools"),
        provider=worker_config.get("provider"),
        model=worker_config.get("model"),
        logger_fn=logger_fn,
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
            judge_resp = await judge_agent.generate_async(judge_prompt, timeout=DEFAULT_AGENT_TIMEOUT, agent_id=stage_name)
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
            current_output = await worker_agent.generate_async(worker_prompt, timeout=DEFAULT_AGENT_TIMEOUT, agent_id=stage_name)

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


def _make_producer_worker(
    config: dict[str, Any],
    logger_fn: Callable[[str, dict[str, Any]], None] | None = None,
    stage_name: str | None = None,
) -> Callable[[Task], Any]:
    """Create a producer worker that discovers items and yields one task per item.

    A producer stage takes input and discovers items from it, yielding one task per
    discovered item. This enables streaming task creation without an agent running
    on each item. Use this when you have a large number of items to process
    (e.g., find 1000 files, then process each one) and you want the downstream
    workers to start immediately rather than waiting for all items to be discovered.

    Config options:
    - discovery: "lines" (default) - split input by newlines
    - filter_empty: bool (default: true) - skip empty lines
    - prefix_pattern: str - regex to strip from items (e.g., r"^(\\d+[\\.\\)]\\s+)")
    """
    discovery_mode = config.get("discovery", "lines")
    filter_empty = config.get("filter_empty", True)
    prefix_pattern = config.get("prefix_pattern")

    async def worker_fn(task: Task):
        """Async-generator worker: discovers items and yields one task per item."""
        input_data = _input_value(task)

        # Discover items based on discovery mode
        items: list[str] = []
        if discovery_mode == "lines":
            # Split input by newlines, optionally filter empties and strip patterns
            raw_lines = [line.strip() for line in str(input_data).strip().splitlines()]
            if filter_empty:
                raw_lines = [line for line in raw_lines if line]
            if prefix_pattern:
                pattern = re.compile(prefix_pattern)
                items = [pattern.sub("", line) for line in raw_lines]
            else:
                items = raw_lines
        else:
            raise ValueError(f"Unknown discovery mode: {discovery_mode}")

        print(f"__LEGO_PRODUCER_INIT__ {len(items)}", flush=True)

        # Yield one task per discovered item
        for i, item in enumerate(items):
            yield Task.create("producer_output", {"result": item, "item_index": i})

    return worker_fn


_WORKER_FACTORIES: dict[
    str,
    Callable[[dict[str, Any], Callable[[str, dict[str, Any]], None] | None, str | None], Callable[[Task], Any]],
] = {
    "agent": _make_agent_worker,
    "fan_out": _make_fan_out_worker,
    "producer": _make_producer_worker,
    "summarize": _make_summarize_worker,
    "judge_loop": _make_judge_loop_worker,
}

# These types manage their own internal concurrency; stage-level max_workers must
# be 1 to prevent multiple tasks from racing on the same queue accounting.
_SINGLE_WORKER_KINDS = frozenset({"fan_out", "producer", "summarize", "judge_loop"})


def build_pipeline_from_yaml(
    workflow_config: dict[str, Any], logger_fn: Callable[[str, dict[str, Any]], None] | None = None
) -> Pipeline:
    """Translate a YAML workflow config dict into a v2 Pipeline.

    Chains are flattened into sequential stages; all other types become
    a single stage. Queue names are assigned as q0, q1, ... qN-1.

    Args:
        workflow_config: The workflow configuration dict
        logger_fn: Optional function to log LLM calls, signature: (event_type: str, details: dict) -> None
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
                worker_fn=factory(sdef.config, logger_fn, sdef.name),
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

    # Set up logging to the same directory as the config file
    run_dir = path.parent
    storage = LegoAgentStorage(run_dir.parent)
    storage.set_run_dir(run_dir)

    def logger_fn(event_type: str, details: dict[str, Any]) -> None:
        storage.log_llm_call(event_type, details)

    pipeline = build_pipeline_from_yaml(workflow_config, logger_fn=logger_fn)
    # Get global max concurrent workers from top-level config or default to 4
    max_concurrent_workers = config.get("max_concurrent_workers", 4)
    await run_pipeline(pipeline, [Task.create("start", {"input": None})], max_concurrent_workers=max_concurrent_workers)


def run_yaml_v2(config_path: str) -> None:
    """Entry point to execute a YAML config via the v2 queue-based runtime."""
    asyncio.run(_run_yaml_v2_async(config_path))
