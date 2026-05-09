from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


_QUEUE_SNAPSHOT_PREVIEW_LIMIT = 20


def _empty_metadata() -> dict[str, Any]:
    return {}


@dataclass
class Task:
    id: str
    type: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=_empty_metadata)

    @staticmethod
    def create(
        task_type: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        return Task(
            id=str(uuid.uuid4()),
            type=task_type,
            payload=payload,
            metadata=metadata or {},
        )


@dataclass
class Stage:
    name: str
    input_queue: str
    output_queue: str | None
    worker_fn: Callable[[Task], Any]
    max_workers: int = 10


@dataclass
class Pipeline:
    stages: list[Stage]

    @property
    def queue_names(self) -> list[str]:
        names: set[str] = set()
        for stage in self.stages:
            names.add(stage.input_queue)
            if stage.output_queue:
                names.add(stage.output_queue)
        return list(names)


def _task_label(task: Task) -> str:
    for key in ("input", "result", "item", "task", "value"):
        value = task.payload.get(key)
        if value not in (None, ""):
            return str(value)
    return task.type


def _task_snapshot(task: Task) -> dict[str, Any]:
    return {"id": task.id, "type": task.type, "label": _task_label(task)}


def _emit_queue_snapshot(
    pipeline: Pipeline,
    queue_state: dict[str, list[Task]],
    active_state: dict[str, list[Task]],
    completed_state: dict[str, int],
    max_concurrent_workers: int,
) -> None:
    stages: list[dict[str, Any]] = []
    for stage in pipeline.stages:
        pending = queue_state.get(stage.input_queue, [])
        active = active_state.get(stage.input_queue, [])
        completed = completed_state.get(stage.input_queue, 0)
        preview = [_task_snapshot(task) for task in pending[:_QUEUE_SNAPSHOT_PREVIEW_LIMIT]]
        active_preview = [_task_snapshot(task) for task in active[:_QUEUE_SNAPSHOT_PREVIEW_LIMIT]]
        stages.append(
            {
                "name": stage.name,
                "input_queue": stage.input_queue,
                "output_queue": stage.output_queue,
                "total": len(pending),
                "active_total": len(active),
                "completed_total": completed,
                "truncated": len(pending) > _QUEUE_SNAPSHOT_PREVIEW_LIMIT,
                "active_truncated": len(active) > _QUEUE_SNAPSHOT_PREVIEW_LIMIT,
                "tasks": preview,
                "active_tasks": active_preview,
            }
        )

    snapshot = {"max_concurrent_workers": max_concurrent_workers, "stages": stages}
    print(f"__LEGO_QUEUE_SNAPSHOT__ {json.dumps(snapshot)}", flush=True)


async def _worker_loop(
    worker_id: int,
    pipeline: Pipeline,
    queues: dict[str, asyncio.Queue[Task]],
    queue_state: dict[str, list[Task]],
    active_state: dict[str, list[Task]],
    completed_state: dict[str, int],
    state_lock: asyncio.Lock,
    max_concurrent_workers: int,
) -> None:
    """Global worker that pulls from any stage's input queue.

    Each worker:
    1. Tries each stage's queue in round-robin order
    2. Picks the first non-empty queue it finds
    3. Processes the task through that stage's worker_fn
    4. Enqueues results to the stage's output queue if present
    5. Repeats until all tasks are done (all queues empty AND all task_done called)
    """
    while True:
        task = None
        stage = None

        # Try to find a task from any stage's input queue (round-robin)
        for s in pipeline.stages:
            try:
                task = queues[s.input_queue].get_nowait()
                stage = s
                async with state_lock:
                    pending = queue_state.get(stage.input_queue, [])
                    if pending:
                        pending.pop(0)
                    active_state.setdefault(stage.input_queue, []).append(task)
                    _emit_queue_snapshot(pipeline, queue_state, active_state, completed_state, max_concurrent_workers)
                break
            except asyncio.QueueEmpty:
                continue

        # If no task found, wait for any queue to have something
        if task is None:
            # Wait for the next task from any stage
            _done, pending = await asyncio.wait(
                [asyncio.create_task(_wait_for_queue(queues[s.input_queue])) for s in pipeline.stages],
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Pick the first stage that has a task
            for s in pipeline.stages:
                try:
                    task = queues[s.input_queue].get_nowait()
                    stage = s
                    break
                except asyncio.QueueEmpty:
                    continue
            # Clean up pending tasks
            for p in pending:
                p.cancel()

        if task and stage:
            print(f"__LEGO_TASK_START__ {stage.name} {task.id}")
            try:
                # Support both regular awaitable worker functions that return a list of
                # tasks, and async-generator workers that yield tasks as they become
                # available (used by fan_out to enable incremental enqueueing).
                worker_result = stage.worker_fn(task)
                if hasattr(worker_result, "__aiter__"):
                    # Async generator: iterate and enqueue each yielded task immediately.
                    async for new_task in worker_result:
                        if stage.output_queue:
                            await queues[stage.output_queue].put(new_task)
                            async with state_lock:
                                queue_state.setdefault(stage.output_queue, []).append(new_task)
                                _emit_queue_snapshot(
                                    pipeline, queue_state, active_state, completed_state, max_concurrent_workers
                                )
                else:
                    results = await worker_result
                    if stage.output_queue:
                        for new_task in results:
                            await queues[stage.output_queue].put(new_task)
                            async with state_lock:
                                queue_state.setdefault(stage.output_queue, []).append(new_task)
                                _emit_queue_snapshot(
                                    pipeline, queue_state, active_state, completed_state, max_concurrent_workers
                                )
                print(f"__LEGO_TASK_DONE__ {stage.name} {task.id}")
            except Exception as e:
                print(f"__LEGO_TASK_ERROR__ {stage.name} {task.id} {e}")
                # TODO: add retry / dead-letter handling
            finally:
                async with state_lock:
                    active_items = active_state.get(stage.input_queue, [])
                    for i, active_task in enumerate(active_items):
                        if active_task.id == task.id:
                            active_items.pop(i)
                            completed_state[stage.input_queue] = completed_state.get(stage.input_queue, 0) + 1
                            break
                    _emit_queue_snapshot(pipeline, queue_state, active_state, completed_state, max_concurrent_workers)
                queues[stage.input_queue].task_done()


async def _wait_for_queue(q: asyncio.Queue[Task]) -> None:
    """Wait until a queue has at least one item."""
    while q.empty():
        await asyncio.sleep(0.01)


def _start_global_workers(
    pipeline: Pipeline,
    queues: dict[str, asyncio.Queue[Task]],
    queue_state: dict[str, list[Task]],
    active_state: dict[str, list[Task]],
    completed_state: dict[str, int],
    max_concurrent_workers: int,
    state_lock: asyncio.Lock,
) -> list[asyncio.Task[None]]:
    """Start a global worker pool that can work on any stage."""
    print(f"__LEGO_PIPELINE_START__ Starting {max_concurrent_workers} global workers")
    return [
        asyncio.create_task(
            _worker_loop(
                worker_index,
                pipeline,
                queues,
                queue_state,
                active_state,
                completed_state,
                state_lock,
                max_concurrent_workers,
            )
        )
        for worker_index in range(max_concurrent_workers)
    ]


async def run_pipeline(
    pipeline: Pipeline,
    initial_tasks: list[Task],
    max_concurrent_workers: int = 4,
) -> None:
    """Execute a pipeline with a global worker pool.

    Args:
        pipeline: The pipeline definition with stages
        initial_tasks: Tasks to start the pipeline with
        max_concurrent_workers: Maximum number of agents working concurrently (default: 4)
    """
    structure = [
        {"name": s.name, "input_queue": s.input_queue, "output_queue": s.output_queue} for s in pipeline.stages
    ]
    print(f"__LEGO_PIPELINE_INIT__ {json.dumps(structure)}", flush=True)
    print(f"__LEGO_MAX_WORKERS__ {max_concurrent_workers}", flush=True)
    for stage in pipeline.stages:
        print(f"__LEGO_STAGE_START__ {stage.name}", flush=True)

    queues: dict[str, asyncio.Queue[Task]] = {name: asyncio.Queue(maxsize=100) for name in pipeline.queue_names}
    queue_state: dict[str, list[Task]] = {name: [] for name in pipeline.queue_names}
    active_state: dict[str, list[Task]] = {name: [] for name in pipeline.queue_names}
    completed_state: dict[str, int] = dict.fromkeys(pipeline.queue_names, 0)
    state_lock = asyncio.Lock()

    for task in initial_tasks:
        await queues[pipeline.stages[0].input_queue].put(task)
        queue_state[pipeline.stages[0].input_queue].append(task)

    _emit_queue_snapshot(pipeline, queue_state, active_state, completed_state, max_concurrent_workers)

    # Start global worker pool that can work on any stage
    workers = _start_global_workers(
        pipeline, queues, queue_state, active_state, completed_state, max_concurrent_workers, state_lock
    )

    # Wait for all queues to be processed
    for stage in pipeline.stages:
        await queues[stage.input_queue].join()
        print(f"__LEGO_STAGE_IDLE__ {stage.name}")

    # Cancel all workers
    for w in workers:
        w.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
