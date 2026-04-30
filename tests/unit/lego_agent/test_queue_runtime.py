import asyncio

from lego_agent.backend.queue_runtime import Pipeline, Stage, Task, run_pipeline


async def test_single_stage_processes_all_tasks():
    received: list[int] = []

    async def worker(task: Task) -> list[Task]:
        received.append(task.payload["value"])
        return []

    stage = Stage("process", "input", None, worker, max_workers=2)
    pipeline = Pipeline(stages=[stage])
    tasks = [Task.create("test", {"value": i}) for i in range(5)]

    await run_pipeline(pipeline, tasks)

    assert sorted(received) == list(range(5))


async def test_two_stage_pipeline_passes_tasks_downstream():
    collected: list[int] = []

    async def double(task: Task) -> list[Task]:
        return [Task.create("doubled", {"value": task.payload["value"] * 2})]

    async def collect(task: Task) -> list[Task]:
        collected.append(task.payload["value"])
        return []

    stages = [
        Stage("double", "raw", "doubled", double, max_workers=2),
        Stage("collect", "doubled", None, collect, max_workers=2),
    ]
    pipeline = Pipeline(stages=stages)
    tasks = [Task.create("raw", {"value": i}) for i in range(3)]

    await run_pipeline(pipeline, tasks)

    assert sorted(collected) == [0, 2, 4]


async def test_worker_exception_does_not_crash_pipeline():
    results: list[str] = []

    async def flaky(task: Task) -> list[Task]:
        if task.payload["fail"]:
            raise ValueError("boom")
        results.append(task.id)
        return []

    stage = Stage("flaky", "in", None, flaky, max_workers=1)
    pipeline = Pipeline(stages=[stage])
    tasks = [
        Task.create("t", {"fail": True}),
        Task.create("t", {"fail": False}),
        Task.create("t", {"fail": False}),
    ]

    await run_pipeline(pipeline, tasks)

    assert len(results) == 2


async def test_max_workers_bounds_concurrency():
    concurrent = 0
    peak = 0

    async def slow(task: Task) -> list[Task]:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.01)
        concurrent -= 1
        return []

    stage = Stage("slow", "in", None, slow, max_workers=3)
    pipeline = Pipeline(stages=[stage])
    tasks = [Task.create("t", {}) for _ in range(10)]

    await run_pipeline(pipeline, tasks)

    assert peak <= 3


async def test_empty_pipeline_completes_immediately():
    stage = Stage("noop", "in", None, lambda t: [], max_workers=1)
    pipeline = Pipeline(stages=[stage])

    await run_pipeline(pipeline, [])


async def test_task_create_assigns_unique_ids():
    ids = {Task.create("x", {}).id for _ in range(100)}
    assert len(ids) == 100


def test_pipeline_derives_queue_names_from_stages():
    stages = [
        Stage("a", "q1", "q2", lambda t: [], max_workers=1),
        Stage("b", "q2", None, lambda t: [], max_workers=1),
    ]
    pipeline = Pipeline(stages=stages)
    assert set(pipeline.queue_names) == {"q1", "q2"}


def test_pipeline_single_stage_no_output_queue():
    stage = Stage("a", "q1", None, lambda t: [], max_workers=1)
    pipeline = Pipeline(stages=[stage])
    assert pipeline.queue_names == ["q1"]


async def test_observability_events_emitted(capsys):
    async def worker(task: Task) -> list[Task]:
        return []

    stage = Stage("my_stage", "in", None, worker, max_workers=1)
    pipeline = Pipeline(stages=[stage])

    await run_pipeline(pipeline, [Task.create("t", {})])

    out = capsys.readouterr().out
    assert "__LEGO_STAGE_START__ my_stage" in out
    assert "__LEGO_TASK_START__ my_stage" in out
    assert "__LEGO_TASK_DONE__ my_stage" in out
    assert "__LEGO_STAGE_IDLE__ my_stage" in out
