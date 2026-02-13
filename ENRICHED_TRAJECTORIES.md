# Enriched Trajectory Data for Prompt Optimization

## Overview

This document explains how to use **enriched trajectories** that link trajectory files with Gemini session data to provide richer training examples for DSPy prompt optimization.

## What Are Enriched Trajectories?

Standard trajectories record:
- Phase information (exploration, deployment, etc.)
- Prompt kwargs (template variables)
- Success/failure status
- Limited message history

**Enriched trajectories** add:
- ✅ Full Gemini conversation history (all tool calls, responses, thoughts)
- ✅ Complete rendered prompts (not just kwargs)
- ✅ Token usage statistics
- ✅ Session-to-call linking via timestamps
- ✅ Multiple prompts per call identified

## Current Status (opt2/opt3 runs)

### Successfully Enriched:
```
opt2: 52 examples total
  - deployer_fix_error: 33 examples ✓
  - code_analyzer_user: 7 examples ✓
  - deployer_generate_deploy_script: 6 examples ✓
  - deployer_generate_health_check: 6 examples ✓

opt3: Similar distribution (not yet processed)
```

### Missing:
```
❌ deployer_summarize: 0 examples
   Reason: ProgressSummarizer was never invoked (bug)
   See "Known Issues" section below
```

## Usage

### 1. Generate Enriched Trajectories

```bash
# Enrich trajectories from opt2 and opt3
python3 scripts/enrich_trajectories_from_sessions.py \
    --input-dirs opt2 opt3 \
    --output-dir enriched_trajectories

# Output: enriched_trajectories/opt2/*/enriched_trajectory.json
```

### 2. Load Examples for Optimization

```python
from app_operator.dspy_integration.enriched_data_loader import (
    EnrichedTrajectoryDataLoader,
    get_example_counts_by_prompt
)

# Load enriched examples
loader = EnrichedTrajectoryDataLoader('enriched_trajectories/opt2')
examples = loader.load_examples(success_only=False)

print(f"Loaded {len(examples)} enriched examples")
print(get_example_counts_by_prompt(examples))

# Use with DSPy optimizer (modify optimizer.py to accept enriched examples)
```

### 3. Use with DSPy Optimization (TODO)

The optimizer needs modification to use `EnrichedTrajectoryExample` instead of `TrajectoryExample`. Key changes needed in `app_operator/dspy_integration/optimizer.py`:

```python
# Instead of:
data_loader = TrajectoryDataLoader(tdir)

# Use:
enriched_loader = EnrichedTrajectoryDataLoader(enriched_tdir)
examples = enriched_loader.load_examples(success_only=False)
```

## Enriched Data Structure

Each `EnrichedTrajectoryExample` contains:

```python
{
    'call_id': 3,
    'phase': Phase.DEPLOYMENT,
    'prompt_name': 'deployer_fix_error',
    'rendered_prompt': 'A deployment has failed. Analyze the error...',
    'prompt_kwargs': {
        'attempt': 1,
        'max_attempts': 30,
        'error_context': '...',
        'deploy_script': '.sds/deploy.sh',
        ...
    },
    'full_conversation': [
        {'type': 'user', 'content': '...'},
        {'type': 'gemini', 'content': '...', 'toolCalls': [...]},
        ...
    ],
    'token_usage': {'total': 12450, 'input': 9037, ...},
    'session_metadata': {
        'session_file': 'abc123_session-2026-02-12T02-58.json',
        'num_messages': 8
    },
    'success': True
}
```

## Benefits Over Standard Trajectories

1. **Complete Context**: Full conversation history shows how the agent reasoned and what tools it used
2. **Reverse-Engineered Kwargs**: Can extract kwargs from rendered prompts when not recorded
3. **Multiple Prompts per Call**: Identifies all prompts used during a single trajectory call
4. **Token Metrics**: Actual token usage for optimization efficiency analysis
5. **Better Debugging**: Can trace exactly what happened in each session

## Known Issues

### Issue 1: `deployer_summarize` Missing (CRITICAL)

**Problem**: 0 examples for `deployer_summarize` in all runs

**Root Cause**: The `ProgressSummarizer` was never invoked despite deployments lasting >15s (up to 631s)

**Evidence**:
- 4 deployment scripts ran for 28-631 seconds (should trigger summaries at 15s, 45s, 75s...)
- 0 Gemini sessions with "summarize", "output_msg", or "output_snippet" keywords
- 0 trajectory entries with `output_snippet` in `prompt_kwargs`

**Impact**: Cannot optimize `deployer_summarize` prompt

**Investigation Needed**:
The code path exists and appears correct:
```python
# subprocess_runner.py lines 467-469
if summarizer.should_summarize():
    recent_output = self.get_recent_output(num_lines=20)
    summarizer.summarize(recent_output)
```

But it's never executing. Possible causes:
1. `should_summarize()` always returns False (timing logic bug?)
2. `summarize()` fails silently (exception caught in line 104-106 of progress_summarizer.py)
3. Agent generate calls during subprocess aren't recorded in trajectory
4. Some condition prevents the monitoring loop from running

**Next Steps**:
1. Add debug logging to `ProgressSummarizer.should_summarize()` and `summarize()`
2. Add logging to subprocess_runner.py monitoring loop
3. Run a test deployment with debug logging enabled
4. Check if `sleep_func(0.1)` loop is actually running during long subprocesses

### Issue 2: Network/DNS Errors (RESOLVED)

The Google Cloud authentication errors during opt2/opt3 runs were temporary DNS failures. Network is working now.

## Scripts

### `scripts/enrich_trajectories_from_sessions.py`
Correlates Gemini sessions with trajectory calls and creates enriched trajectory files.

**Features**:
- Timestamp-based session-to-call matching
- Automatic prompt type identification
- Extracts full conversation history
- Preserves original trajectory structure

### `app_operator/dspy_integration/enriched_data_loader.py`
Loads enriched trajectories for DSPy optimization.

**Features**:
- Drops in place of TrajectoryDataLoader
- Reverse-engineers kwargs from rendered prompts when needed
- Filters by phase, success status
- Provides example counts by prompt type

## Future Work

1. **Integrate with Optimizer**: Modify `optimizer.py` to use enriched examples
2. **Fix ProgressSummarizer**: Debug and fix the missing summarize calls
3. **Enrich opt3 Data**: Run enrichment on opt3 trajectories
4. **Add Validation**: Verify enriched kwargs match original template expectations
5. **Streaming Enrichment**: Enrich trajectories in real-time during runs
6. **Session Replay**: Use enriched data to replay exact agent interactions for debugging

## Example: Using Enriched Data

```python
# Load enriched examples
from app_operator.dspy_integration.enriched_data_loader import EnrichedTrajectoryDataLoader
from app_operator.trajectory import Phase

loader = EnrichedTrajectoryDataLoader('enriched_trajectories/opt2')

# Get only deployment phase examples
deployment_examples = loader.load_examples(
    success_only=False,
    phase=Phase.DEPLOYMENT
)

# Filter for specific prompt
fix_error_examples = [
    ex for ex in deployment_examples
    if ex.prompt_name == 'deployer_fix_error'
]

print(f"Found {len(fix_error_examples)} deployer_fix_error examples")

# Access full conversation
for ex in fix_error_examples[:3]:
    print(f"\nCall {ex.call_id}:")
    print(f"  Attempt: {ex.prompt_kwargs.get('attempt')}")
    print(f"  Tool calls: {sum(1 for m in ex.full_conversation if 'toolCalls' in m)}")
    print(f"  Tokens: {ex.token_usage.get('total', 0)}")
    print(f"  Success: {ex.success}")
```

## Questions?

See the investigation notes in this conversation for detailed analysis of the trajectory → session correlation and the deployer_summarize bug.
