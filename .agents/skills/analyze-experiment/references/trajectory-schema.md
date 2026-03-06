# Trajectory JSON Schema

## Top-Level Structure

```json
{
  "metadata": {
    "repo_path": "string — path to the deployed repository",
    "start_time": "string — ISO datetime",
    "end_time": "string — ISO datetime",
    "agent_name": "string — e.g. 'GeminiGenerationSession', 'LangGraph', 'Agentflow'",
    "status": "string — 'running' | 'completed' | 'failed' | 'interrupted'",
    "run_id": "string — YYYYMMDD-HHMMSS",
    "token_usage": "object (optional) — {input_tokens, output_tokens, total_tokens}",
    "fault_injection": "object (optional) — fault injection metadata"
  },
  "calls": [
    {
      "call_id": "int — sequential (1, 2, 3...)",
      "phase": "string — 'exploration' | 'script_generation' | 'deployment' | 'monitoring'",
      "start_time": "string — ISO datetime",
      "end_time": "string | null",
      "context": "object — phase-specific (e.g. {attempt: 1}, {cycle: 5})",
      "prompt_version": "string (optional) — 'jinja2' or 'dspy_vN'",
      "fallback_occurred": "bool (optional)"
    }
  ],
  "exploration": ["array of ConversationEntry — code analysis phase"],
  "script_generation": ["array of ConversationEntry — deploy/health script creation"],
  "deployment": ["array of ConversationEntry — deployment attempts with self-healing"],
  "monitoring": ["array of ConversationEntry — health check monitoring"],
  "gemini_sessions": ["array of strings — paths to Gemini CLI session files (if used)"]
}
```

## ConversationEntry

Each phase array contains conversation entries, one per agent invocation in that phase:

```json
{
  "call_id": "int — links to calls[] entry",
  "messages": ["array of TrajectoryMessage"],
  "prompt_version": "string (optional)",
  "fallback_occurred": "bool (optional)",
  "prompt_kwargs": "object (optional) — rendered prompt parameters",
  "rendered_prompt": "string (optional) — final prompt sent to agent"
}
```

## TrajectoryMessage

```json
{
  "role": "string — 'system' | 'user' | 'assistant' | 'tool_call'",
  "content": "string (optional) — message text",
  "timestamp": "string — ISO datetime",
  "tool": "string (optional, tool_call only) — tool name (e.g. 'bash', 'Read', 'Grep')",
  "args": "object (optional, tool_call only) — tool arguments",
  "stdout": "string (optional, tool_call only) — stdout output (truncated to ~10000 chars)",
  "stderr": "string (optional, tool_call only) — stderr output",
  "exit_code": "int (optional, tool_call only)",
  "duration_seconds": "float (optional)"
}
```

## Phases Explained

| Phase | Description | Context Fields |
|-------|-------------|----------------|
| `exploration` | CodeAnalyzerAgent reads the codebase, produces `code_analysis.md` and `deployment_issues.md` | — |
| `script_generation` | DeploymentAgent generates `deploy.sh` and `health_check.sh` | — |
| `deployment` | Self-healing loop: run deploy → health check → fix errors → retry. Each attempt is a separate ConversationEntry | `{attempt: N}` |
| `monitoring` | AppMonitor runs periodic health checks after successful deployment | `{cycle: N}` |

## Reading Trajectories Efficiently

Trajectories can be large. Focus on:

1. **`metadata.status`** — did it complete or fail?
2. **`calls[]`** — count phases, check for repeated deployment attempts
3. **`deployment[]`** — the core analysis target; each entry is one attempt
4. **Tool calls with non-zero exit_code** — these are the errors the agent had to fix
5. **Assistant messages after errors** — shows agent's reasoning and fix strategy
6. **`context.attempt`** in calls — tracks attempt number progression

### Useful jq queries

```bash
# Count deployment attempts
jq '.deployment | length' trajectory.json

# List all tool calls with failures
jq '[.deployment[].messages[] | select(.role=="tool_call" and .exit_code != 0 and .exit_code != null)] | length' trajectory.json

# Get metadata summary
jq '.metadata | {status, agent_name, start_time, end_time}' trajectory.json

# Extract fix summaries (assistant messages in deployment phase)
jq '.deployment[].messages[] | select(.role=="assistant") | .content[:200]' trajectory.json
```
