# Streaming Tasks: How Any Worker Can Discover and Emit Tasks Incrementally

## Core Principle

The lego_agent pipeline runtime supports **true streaming discovery** where workers emit tasks as they find them, not after everything is discovered.

Two mechanisms:

1. **Iterative Agent Discovery** - Agent makes multiple LLM calls to discover items in batches
2. **Async Generator Workers** - Any worker can `yield Task` instead of `return [Task]`

Key result: **Downstream workers start immediately, discovery and processing overlap in parallel**.

## Problem We're Solving

### ❌ Bad: Single Agent Call (Batching)
```
LLM Call 1: "Find all READMEs" → Waits... Waits... Waits...
                                   ↓ (after 30 seconds)
                            Returns 1500 paths
                                   ↓
                         Tasks finally created
                                   ↓
                          Workers start processing
                          (30 second delay!)
```

### ✓ Good: Iterative Agent (Streaming)
```
LLM Call 1: "Find first batch" → Returns 25 paths → Tasks created → Workers start
LLM Call 2: "Find next batch"  → Returns 25 paths → Tasks created → More workers process
LLM Call 3: "Find next batch"  → Returns 25 paths → Tasks created → Continue...
(parallel discovery + processing, no wait!)
```

## Solution: Iterative Discovery Mode

Make the agent itself iterative. Each LLM call discovers a batch, yields it immediately, then asks for the next batch:

```yaml
- type: "agent"
  instruction: "Find README files using find_files tool..."
  tools: ["find_files"]
  emit_mode: "iterative_discovery"  # Multiple LLM calls, stream per-batch
```

The agent worker will:
1. Call LLM: "Find first batch of READMEs (up to 25)"
2. Parse response, yield each item as a Task
3. Call LLM: "Find next batch (not already found)"
4. Parse response, yield each item
5. Repeat until LLM says "DONE"

**Result**: Files get queued as batches are discovered, workers start immediately.

## Architecture

```
Discovery Agent (iterative)  →  Task Queue  ←  Worker Pool
├─ Call 1: Batch 1 (25 items)  │            ├─ Worker 1: Processing item 5
├─ Call 2: Batch 2 (25 items)  ├─ Queue     ├─ Worker 2: Processing item 12
├─ Call 3: Batch 3 (25 items)  │ (FIFO)     ├─ Worker 3: Processing item 23
└─ (overlapping discovery)      │ (1000s of ├─ Worker 4: Processing item 34
                                │  tasks)   └─ ...
                                │            
                                └─→ STEP_COMPLETE signal
                                    (queued last, processed after all items)
                                    ↓
                                 Downstream workers
                                 (summarize, etc.)
                                 buffer items until
                                 STEP_COMPLETE arrives
```

**FIFO Guarantee**: Each stage's input queue processes tasks in FIFO order. This ensures:
- Discovery items arrive in discovery order
- `STEP_COMPLETE` signal (queued after all discovery items) is processed **after** all items
- Downstream stages (fan_out, summarize) buffer items and only finalize when `STEP_COMPLETE` arrives

## Comparison: Batch vs. Iterative vs. Fan-Out

| Mode | Type | LLM Calls | When to Use | Speed |
|------|------|-----------|------------|-------|
| **Batch** | agent | 1 | Single output, not discovery | Slow for discovery |
| **Iterative Discovery** | agent | Multiple | Finding 100s-1000s items | Fast, streaming |
| **Fan-Out** | fan_out | N (one per item) | Process each item with LLM | Medium (depends on item count) |

## Real Example: README Discovery

### The Problem (1500+ READMEs)
```yaml
- type: "agent"
  instruction: "Find all README files in the repo"
  tools: ["find_files"]
```

With default batch mode, agent runs once, returns all 1500 paths at once. Downstream workers wait 30 seconds. Bad!

### The Solution
```yaml
- type: "agent"
  instruction: "Find README files..."
  tools: ["find_files"]
  emit_mode: "iterative_discovery"

- type: "fan_out"
  agent:
    instruction: "Read {input} and return content"
    tools: ["read_file"]
```

Now:
- Agent Call 1: Discover 25 READMEs → Yield 25 tasks → Workers 1-4 start reading
- Agent Call 2: Discover 25 more → Yield 25 tasks → Workers continue
- Agent Call 3: Discover 25 more → Workers keep going...

**Total time: ~20 seconds instead of ~30+ seconds** (discovery + processing overlap)

## For Custom Workers: Streaming via Async Generators

You don't need LLMs for streaming. Any worker can yield tasks:

```python
async def file_processor_worker(task: Task):
    """Worker that processes items and yields new tasks."""
    file_path = task.payload["result"]
    content = read_file(file_path)
    
    # Parse content, discover sub-items
    for line_num, line in enumerate(content.split("\n")):
        if is_relevant(line):
            yield Task.create(
                "relevant_line",
                {"file": file_path, "line_num": line_num, "content": line}
            )
    
    # Signal downstream that this stage is complete
    # This lets downstream workers (summarize, etc.) know to finalize
    yield Task.create(
        "STEP_COMPLETE",
        {"source": "file_processor"},
        metadata={"is_completion_signal": True}
    )
```

The queue_runtime detects `hasattr(worker_result, "__aiter__")` and enqueues each yield immediately.

## The STEP_COMPLETE Signal

**What**: A special task type (`"STEP_COMPLETE"`) that signals "all work from this stage is done".

**When**: Emitted by stages that discover or process items (agent with `emit_mode: "iterative_discovery"`, fan_out with streaming, producer, etc.).

**How**: 
1. Stage discovers/produces items and yields them one-by-one
2. When discovery is exhausted, stage yields a `STEP_COMPLETE` task
3. `STEP_COMPLETE` is queued **after** all item tasks (FIFO guarantee)
4. Downstream workers buffer items until they receive `STEP_COMPLETE`
5. Only then do they finalize and emit their own results

**Why**: Coordinates discovery and processing. Without it, a downstream worker (summarize) wouldn't know when to stop buffering and produce output.

**Example Flow**:
```
iterative_discovery Agent:
  yield Task(type="agent_output", payload={"result": "file1.py"})  # item 1
  yield Task(type="agent_output", payload={"result": "file2.py"})  # item 2
  ...
  yield Task(type="STEP_COMPLETE")  # signal: discovery done

Fan_Out Receives:
  - Processes items immediately (parallel)
  - Buffers STEP_COMPLETE until all workers finish
  - Passes STEP_COMPLETE to next stage

Summarize Receives:
  - Buffers each fan_out_output result
  - On STEP_COMPLETE: generates summary from all buffered items
  - Yields final result
```

## For Custom Workers: Streaming via Async Generators

You don't need LLMs for streaming. Any worker can yield tasks:

```python
async def file_processor_worker(task: Task):
    """Worker that processes items and yields new tasks."""
    file_path = task.payload["result"]
    content = read_file(file_path)
    
    # Parse content, discover sub-items
    for line_num, line in enumerate(content.split("\n")):
        if is_relevant(line):
            yield Task.create(
                "relevant_line",
                {"file": file_path, "line_num": line_num, "content": line}
            )
```

The queue_runtime detects `hasattr(worker_result, "__aiter__")` and enqueues each yield immediately.

## Configuration

### Iterative Discovery Agent
```yaml
- type: "agent"
  instruction: "Find items using your tools..."
  tools: ["find_files", "search"]
  emit_mode: "iterative_discovery"   # Use multiple LLM calls
```

The agent will:
- Call 1: "Find first batch (up to 25)"
- Call 2: "Find next batch (not already found)"
- Continue until response contains "DONE"

### Regular Batch Agent
```yaml
- type: "agent"
  instruction: "Process this input..."
  tools: ["search"]
  # emit_mode defaults to "batch" - single LLM call
```

## Why This Works

1. **Multiple LLM calls** - Each call handles a batch (25 items), faster than single call for 1500
2. **Immediate task enqueuing** - Each batch is yielded right after the LLM responds
3. **Worker parallelism** - With 4 workers, items 1-4 process while agent discovers items 25-50
4. **Natural batching** - ~25 items per batch is a practical balance between call count and payload size

## Summary

**Use `emit_mode: "iterative_discovery"` when:**
- Discovering 100s or 1000s of items
- Discovery requires LLM + tools (not just file listing)
- You want workers to start before all items are found

**Default batch mode when:**
- Single output (not discovery)
- Few items to process
- No need for parallelism

