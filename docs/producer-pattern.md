# Producer Pattern Guide

## Problem
When you have 1000+ items to process (e.g., README files, test files), you want:
- Downstream workers to **start immediately** as items are discovered
- No need to wait for all items to be found before processing begins
- Clean, reusable pattern for both script-based and LLM-based discovery

## Solution: The Producer Pattern

The producer pattern separates **discovery** from **processing**:

1. **Discovery stage**: finds items and yields them one at a time
2. **Queue**: each discovered item becomes a task waiting for workers
3. **Workers**: process items in parallel as soon as they're enqueued

## How It Works

### Minimal Example

```yaml
workflow:
  type: "chain"
  steps:
    # Step 1: Agent discovers items (outputs one per line)
    - type: "agent"
      instruction: "Find all files matching pattern. Output paths, one per line."
      tools: ["find_files"]

    # Step 2: Producer converts output into tasks
    - type: "producer"
      discovery: "lines"      # Split by newlines
      filter_empty: true      # Skip empty lines

    # Step 3: Workers process each item immediately
    - type: "fan_out"
      agent:
        instruction: "Process item: {input}"
        tools: ["read_file"]
```

### Key Concepts

- **Producer stage**: Reads agent output and yields one task per line
- **Lines immediately get enqueued**: Worker pool can start consuming while agent is still discovering
- **No batching**: Each item becomes a task as it's discovered
- **Parallelism from discovery**: If you have 4 workers and 1000 items, the first items start processing before item 1000 is found

## Real Use Cases

### Use Case 1: Find and Read All READMEs

```yaml
- type: "agent"
  instruction: "Find all README files. Output paths, one per line."
  tools: ["find_files"]

- type: "producer"
  discovery: "lines"
  filter_empty: true

- type: "fan_out"
  agent:
    instruction: "Read {input} and return full content"
    tools: ["read_file"]
```

**Why this works**: Even if there are 1000 README files, the first few get read while the find_files agent is still listing the rest.

### Use Case 2: Filter Tests by Relevance

```yaml
- type: "agent"
  instruction: "Find all test files. Output paths, one per line."
  tools: ["find_files"]

- type: "producer"
  discovery: "lines"
  filter_empty: true

- type: "fan_out"
  agent:
    instruction: "Read test at {input}. Does it test REST APIs? Answer: YES or NO"
    tools: ["read_file"]

- type: "summarize"
  instruction: "Count how many tests cover REST APIs"
```

**Why this works**: Worker agents can start reading and classifying tests immediately, rather than waiting for all test paths to be discovered.

## Implementation Details

The producer stage:
- Takes output from previous stage (usually a string)
- Parses it based on discovery mode (currently: "lines")
- Yields one `Task` per discovered item
- The queue runtime enqueues each task immediately
- Global worker pool picks up and processes tasks in parallel

## Configuration Options

```yaml
- type: "producer"
  discovery: "lines"              # Required: how to discover items
                                  # Options: "lines" (split by \n)
  filter_empty: true              # Optional: skip empty lines (default: true)
  prefix_pattern: "^\d+\.\s+"    # Optional: regex to strip from items
```

## Comparison: Batch vs. Streaming

### Batch (Old Pattern)
```
Agent generates output → All results collected → fan_out processes all → Takes 30s
                                                                         ↑ workers idle
```

### Streaming with Producer (New Pattern)
```
Agent discovers item 1 → Producer yields task 1 → Worker 1 starts
Agent discovers item 2 → Producer yields task 2 → Worker 2 starts  (parallelism!)
Agent discovering... → Producer still yielding → Workers processing...
                       Total time: ~20s (faster)
```

## When to Use Producer vs. fan_out

| Pattern | Use When |
|---------|----------|
| `producer` | You need to **discover** items from a list/string. Simple splitting or parsing. |
| `fan_out` | You need to **run an agent** on each item (e.g., read file, classify, summarize). |
| Both together | Discover items (producer) → Process items with agent (fan_out) |

## Example: Your README Workflow

**Before** (batching problem):
```yaml
- type: "agent"                    # Finds 1500 READMEs, outputs all at once
  instruction: "Find all READMEs"
  tools: ["find_files"]

- type: "fan_out"                  # Must wait for all 1500 before processing
  agent:
    instruction: "Read {input}"
    tools: ["read_file"]
```

**After** (streaming solution):
```yaml
- type: "agent"                    # Outputs paths as found
  instruction: "Find all READMEs"
  tools: ["find_files"]

- type: "producer"                 # Emits tasks incrementally
  discovery: "lines"
  filter_empty: true

- type: "fan_out"                  # Processes each as it arrives
  agent:
    instruction: "Read {input}"
    tools: ["read_file"]
```

Workers can now start reading READMEs while the find_files agent is still discovering more.

## Supported Workflows

The producer pattern works with any downstream processors:
- `fan_out` + `agent` (process with LLM)
- `summarize` (collect and synthesize)
- `judge_loop` (iterative refinement)
- Chained stages (multiple levels of discovery and processing)
