from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass
class Task:
    id: str
    type: str
    payload: dict
    metadata: dict = field(default_factory=dict)

    @staticmethod
    def create(task_type: str, payload: dict, metadata: dict | None = None) -> Task:
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
    worker_fn: Callable[[Task], Awaitable[list[Task]]]
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


async def _worker_loop(stage: Stage, queues: dict[str, asyncio.Queue]) -> None:
    while True:
        task = await queues[stage.input_queue].get()
        print(f"__LEGO_TASK_START__ {stage.name} {task.id}")
        try:
            results = await stage.worker_fn(task)
            if stage.output_queue:
                for new_task in results:
                    await queues[stage.output_queue].put(new_task)
            print(f"__LEGO_TASK_DONE__ {stage.name} {task.id}")
        except Exception:
            # TODO: add retry / dead-letter handling
            pass
        finally:
            queues[stage.input_queue].task_done()


def _start_stage(stage: Stage, queues: dict[str, asyncio.Queue]) -> list[asyncio.Task]:
    print(f"__LEGO_STAGE_START__ {stage.name}")
    return [asyncio.create_task(_worker_loop(stage, queues)) for _ in range(stage.max_workers)]


async def run_pipeline(pipeline: Pipeline, initial_tasks: list[Task]) -> None:
    structure = [
        {"name": s.name, "input_queue": s.input_queue, "output_queue": s.output_queue}
        for s in pipeline.stages
    ]
    print(f"__LEGO_PIPELINE_INIT__ {json.dumps(structure)}", flush=True)

    queues: dict[str, asyncio.Queue] = {name: asyncio.Queue(maxsize=100) for name in pipeline.queue_names}

    for task in initial_tasks:
        await queues[pipeline.stages[0].input_queue].put(task)

    workers: list[asyncio.Task] = []
    for stage in pipeline.stages:
        workers.extend(_start_stage(stage, queues))

    # Join stages in order: each join completes only after all outputs for that
    # stage have been enqueued into the next queue (task_done() follows put()).
    for stage in pipeline.stages:
        await queues[stage.input_queue].join()
        print(f"__LEGO_STAGE_IDLE__ {stage.name}")

    for w in workers:
        w.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
