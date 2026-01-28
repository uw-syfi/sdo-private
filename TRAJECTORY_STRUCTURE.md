# Trajectory Structure with Sequential Call IDs

## Overview

The SDS trajectory recording system now includes sequential call IDs to track and correlate agent interactions across different phases. This makes it easy to trace specific calls and connect trajectory data with external session logs (e.g., Gemini CLI sessions).

## Key Features

- **Sequential Call IDs**: Each agent call receives a unique ID (1, 2, 3, ...)
- **Call Tracking**: All calls are tracked in a top-level `calls` array with metadata
- **Phase Correlation**: Each conversation in phases includes its `call_id`
- **Session Matching**: Gemini CLI sessions are automatically matched to call IDs
- **Metadata Files**: For Gemini agent, metadata files link sessions to trajectory calls

## Trajectory JSON Structure

```json
{
  "metadata": {
    "repo_path": "/path/to/repo",
    "start_time": "2025-01-28 10:30:00",
    "end_time": "2025-01-28 10:35:00",
    "agent_name": "gemini",
    "status": "completed",
    "run_id": "20250128-103000"
  },
  "calls": [
    {
      "call_id": 1,
      "phase": "script_generation",
      "start_time": "2025-01-28 10:30:05",
      "end_time": "2025-01-28 10:30:45",
      "context": {}
    },
    {
      "call_id": 2,
      "phase": "deployment",
      "start_time": "2025-01-28 10:31:00",
      "end_time": "2025-01-28 10:32:30",
      "context": {
        "attempt": 1,
        "max_attempts": 5
      }
    },
    {
      "call_id": 3,
      "phase": "deployment",
      "start_time": "2025-01-28 10:32:35",
      "end_time": "2025-01-28 10:33:15",
      "context": {
        "attempt": 2,
        "max_attempts": 5
      }
    },
    {
      "call_id": 4,
      "phase": "monitoring",
      "start_time": "2025-01-28 10:33:20",
      "end_time": "2025-01-28 10:34:00",
      "context": {
        "cycle": 1
      }
    }
  ],
  "script_generation": [
    {
      "call_id": 1,
      "messages": [
        {
          "role": "system",
          "content": "You are an AI operator...",
          "timestamp": "2025-01-28 10:30:05"
        },
        {
          "role": "user",
          "content": "Generate deployment scripts",
          "timestamp": "2025-01-28 10:30:06"
        },
        {
          "role": "tool_call",
          "tool": "ls",
          "args": {"path": "."},
          "stdout": "file1.txt\nfile2.txt",
          "timestamp": "2025-01-28 10:30:10"
        },
        {
          "role": "assistant",
          "content": "I've analyzed the repository...",
          "timestamp": "2025-01-28 10:30:45"
        }
      ]
    }
  ],
  "deployment": [
    {
      "call_id": 2,
      "messages": [...]
    },
    {
      "call_id": 3,
      "messages": [...]
    }
  ],
  "monitoring": [
    {
      "call_id": 4,
      "messages": [...]
    }
  ],
  "gemini_sessions": [
    {
      "session_file": "trajectories/gemini_sessions/20250128-103000/gemini_session_call_001.json",
      "original_path": "/Users/user/.gemini/tmp/project/chats/session-12345.json",
      "modified_time": "2025-01-28 10:30:45",
      "call_id": 1
    },
    {
      "session_file": "trajectories/gemini_sessions/20250128-103000/gemini_session_call_002.json",
      "original_path": "/Users/user/.gemini/tmp/project/chats/session-12346.json",
      "modified_time": "2025-01-28 10:32:30",
      "call_id": 2
    }
  ]
}
```

## File Organization

When you run the operator, files are organized as follows:

```
<repo>/.sds/
├── trajectory.json                          # Symlink to latest trajectory
├── trajectories/
│   ├── trajectory_20250128-103000.json     # Timestamped trajectory file
│   └── gemini_sessions/
│       └── 20250128-103000/                 # Sessions for this run
│           ├── gemini_session_call_001.json # Session for call_id 1
│           ├── gemini_session_call_002.json # Session for call_id 2
│           └── sds_call_001.json            # Metadata file (created by agent)
```

## Using Call IDs

### In Code

```python
from app_operator.trajectory import (
    init_trajectory,
    record_phase_start,
    get_current_call_id,
    get_run_id,
    Phase,
)

# Initialize trajectory recording
init_trajectory(repo_path)

# Start a phase (automatically gets a sequential call ID)
record_phase_start(Phase.DEPLOYMENT, context={"attempt": 1})

# Get the current call ID
call_id = get_current_call_id()  # Returns 1, 2, 3, etc.

# Get the run ID (timestamp)
run_id = get_run_id()  # Returns "20250128-103000"
```

### Correlating with Gemini Sessions

When using the Gemini agent:

1. Each agent call creates a metadata file in `~/.gemini/tmp/<project>/chats/sds_call_XXX.json`
2. This metadata includes the `call_id` and `run_id`
3. When the trajectory is finalized, Gemini session files are collected and matched to call IDs
4. Session files are renamed to `gemini_session_call_XXX.json` for easy identification
5. The `gemini_sessions` array in trajectory.json shows the mapping

### Analysis Example

```python
import json

# Load trajectory
with open(".sds/trajectory.json") as f:
    trajectory = json.load(f)

# Find a specific call
call_id = 2
call_info = next(c for c in trajectory["calls"] if c["call_id"] == call_id)
print(f"Call {call_id}: {call_info['phase']} from {call_info['start_time']}")

# Find the conversation for this call
for conversation in trajectory["deployment"]:
    if conversation["call_id"] == call_id:
        print(f"Messages: {len(conversation['messages'])}")

# Find the Gemini session for this call
for session in trajectory.get("gemini_sessions", []):
    if session["call_id"] == call_id:
        print(f"Session file: {session['session_file']}")
```

## Benefits

1. **Traceability**: Easily trace a specific agent interaction through all logs
2. **Debugging**: Quickly find and analyze failed calls
3. **Correlation**: Connect trajectory data with external session logs
4. **Analysis**: Understand the sequence and timing of agent operations
5. **Reproducibility**: Reference specific calls when reporting issues
