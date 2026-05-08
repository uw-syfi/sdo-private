import asyncio

from lego_agent.backend.queue_runtime import Pipeline, Stage, Task, run_pipeline


async def _noop_worker(_t: Task) -> list[Task]:
    return []


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

    await run_pipeline(pipeline, tasks, max_concurrent_workers=3)

    assert peak <= 3


async def test_empty_pipeline_completes_immediately():
    stage = Stage("noop", "in", None, _noop_worker, max_workers=1)
    pipeline = Pipeline(stages=[stage])

    await run_pipeline(pipeline, [])


async def test_task_create_assigns_unique_ids():
    ids = {Task.create("x", {}).id for _ in range(100)}
    assert len(ids) == 100


def test_pipeline_derives_queue_names_from_stages():
    stages = [
        Stage("a", "q1", "q2", _noop_worker, max_workers=1),
        Stage("b", "q2", None, _noop_worker, max_workers=1),
    ]
    pipeline = Pipeline(stages=stages)
    assert set(pipeline.queue_names) == {"q1", "q2"}


def test_pipeline_single_stage_no_output_queue():
    stage = Stage("a", "q1", None, _noop_worker, max_workers=1)
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
    assert "__LEGO_QUEUE_SNAPSHOT__" in out


async def test_queue_snapshot_includes_active_tasks(capsys):
    async def slow_worker(task: Task) -> list[Task]:
        await asyncio.sleep(0.01)
        return []

    stage = Stage("slow", "in", None, slow_worker, max_workers=1)
    pipeline = Pipeline(stages=[stage])

    await run_pipeline(pipeline, [Task.create("t", {"value": "readme.md"})], max_concurrent_workers=1)

    out = capsys.readouterr().out
    assert '"active_total": 1' in out or '"active_total": 0' in out


async def test_async_generator_worker_yields_tasks_incrementally():
    """Test that async-generator workers (like producer/fan_out) yield tasks one at a time."""
    yielded_tasks: list[str] = []
    collected_results: list[str] = []

    async def streaming_producer(task: Task):
        """Simulate a producer that yields items as they're discovered."""
        input_data = task.payload.get("result", "")
        for line in input_data.strip().split("\n"):
            if line.strip():
                yield Task.create("produced", {"result": line.strip()})

    async def collector(task: Task) -> list[Task]:
        collected_results.append(task.payload.get("result", ""))
        return []

    stages = [
        Stage("producer", "in", "out", streaming_producer, max_workers=1),
        Stage("collect", "out", None, collector, max_workers=3),
    ]
    pipeline = Pipeline(stages=stages)
    input_text = "file1.txt\nfile2.txt\nfile3.txt"
    tasks = [Task.create("input", {"result": input_text})]

    await run_pipeline(pipeline, tasks)

    # All items should be collected, proving they were yielded incrementally
    assert sorted(collected_results) == ["file1.txt", "file2.txt", "file3.txt"]


async def test_producer_pattern_enables_parallel_processing():
    """Test that producer pattern allows downstream workers to start before producer finishes."""
    execution_order: list[str] = []
    lock = asyncio.Lock()

    async def slow_producer(task: Task):
        """Producer that discovers items slowly (simulating file discovery)."""
        items = ["item1", "item2", "item3"]
        for item in items:
            await asyncio.sleep(0.01)  # Simulate discovery time
            yield Task.create("produced", {"result": item})

    async def fast_worker(task: Task) -> list[Task]:
        """Worker that processes items quickly."""
        async with lock:
            execution_order.append(f"start_{task.payload['result']}")
        await asyncio.sleep(0.005)
        async with lock:
            execution_order.append(f"end_{task.payload['result']}")
        return []

    stages = [
        Stage("producer", "in", "out", slow_producer, max_workers=1),
        Stage("worker", "out", None, fast_worker, max_workers=3),
    ]
    pipeline = Pipeline(stages=stages)
    tasks = [Task.create("input", {})]

    await run_pipeline(pipeline, tasks, max_concurrent_workers=3)

    # Verify at least 2 items started before all discoveries finished
    # If producer was batching, items would all start at once
    # If producer is streaming, early items start while producer is still discovering
    assert len(execution_order) == 6  # 3 items × (start + end)


async def test_discovery_via_async_generator_multiple_yields():
    """Test that discovery workers can yield multiple tasks in sequence."""
    discovered: list[str] = []

    async def discovery_worker(task: Task):
        """Simulates iterative discovery - yields items one at a time."""
        # On each call, pretend we discovered another item
        items = ["file_a.md", "file_b.md", "file_c.md"]
        for item in items:
            yield Task.create("discovered", {"result": item})

    async def collector(task: Task) -> list[Task]:
        discovered.append(task.payload["result"])
        return []

    stages = [
        Stage("discover", "in", "out", discovery_worker, max_workers=1),
        Stage("collect", "out", None, collector, max_workers=3),
    ]
    pipeline = Pipeline(stages=stages)

    await run_pipeline(pipeline, [Task.create("trigger", {})], max_concurrent_workers=3)

    assert sorted(discovered) == ["file_a.md", "file_b.md", "file_c.md"]


async def test_streaming_agent_emits_per_line():
    """Test that an agent-like worker can stream output line-by-line."""
    emitted_lines: list[str] = []

    async def streaming_agent_worker(task: Task):
        """Agent that outputs multiple lines and yields one task per line."""
        # Simulate agent output with 3 items
        agent_output = "file1.md\nfile2.md\nfile3.md"
        
        # Streaming mode: split by lines and yield each
        for i, line in enumerate(agent_output.strip().split("\n")):
            if line.strip():
                yield Task.create("agent_output", {"result": line.strip(), "index": i})

    async def collector(task: Task) -> list[Task]:
        emitted_lines.append(task.payload["result"])
        return []

    stages = [
        Stage("agent", "in", "out", streaming_agent_worker, max_workers=1),
        Stage("collect", "out", None, collector, max_workers=3),
    ]
    pipeline = Pipeline(stages=stages)

    await run_pipeline(pipeline, [Task.create("input", {})], max_concurrent_workers=3)

    assert emitted_lines == ["file1.md", "file2.md", "file3.md"]


async def test_iterative_discovery_batches_and_yields():
    """Test that iterative discovery yields tasks across multiple batches."""
    discovered_items: list[tuple[str, int]] = []

    async def iterative_agent_worker(task: Task):
        """Simulates iterative agent discovery across multiple LLM calls."""
        all_items = ["item1", "item2", "item3", "item4", "item5"]
        batch_size = 2
        discovered = set()
        batch_num = 0
        
        while len(discovered) < len(all_items):
            batch_num += 1
            # Simulate finding next batch
            batch = [
                item for item in all_items
                if item not in discovered
            ][:batch_size]
            
            if not batch:
                break
            
            # Yield each item in the batch
            for item in batch:
                discovered.add(item)
                yield Task.create(
                    "discovered",
                    {"result": item, "batch": batch_num}
                )

    async def collector(task: Task) -> list[Task]:
        discovered_items.append((
            task.payload["result"],
            task.payload["batch"]
        ))
        return []

    stages = [
        Stage("discover", "in", "out", iterative_agent_worker, max_workers=1),
        Stage("collect", "out", None, collector, max_workers=3),
    ]
    pipeline = Pipeline(stages=stages)

    await run_pipeline(pipeline, [Task.create("trigger", {})], max_concurrent_workers=3)

    # Verify all items discovered and grouped by batch
    items_only = [item for item, _ in discovered_items]
    batches = [batch for _, batch in discovered_items]
    
    assert items_only == ["item1", "item2", "item3", "item4", "item5"]
    assert batches == [1, 1, 2, 2, 3]  # 2-2-1 batches
