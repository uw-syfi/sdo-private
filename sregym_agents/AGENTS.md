# SRE Gym Agents

Agents that compete in the SRE Gym environment (fault diagnosis and remediation benchmarks).

## Agents

Registered in `agents.yaml`. Each agent has a `kickoff_command` run from the repo root.

- **crucible** — `uv run python -m sregym_agents.crucible.driver`
- **cli_agent** — `uv run python -m sregym_agents.cli_agent` (minimal `agentshim` wrapper)

## Architecture

- `crucible/` — Crucible-environment agent (pydantic-ai: orchestrator, judge, SRE agent, tools)
- `cli_agent/` — Thin wrapper around `agentshim` CLI agents (claude / codex / gemini / opencode); single-shot per stage
- `cli_agent/memory/` — Optional persistent incident memory (per-app JSONL lessons + `recall` MCP tool). Off by default; enable with `memory_enabled = true` in the `[agent.cli_agent]` config block. Design: [`docs/cli-agent-memory.md`](../docs/cli-agent-memory.md).
- `cli_agent/trajectory/` — Optional raw-trajectory recording + retrieval (per-app JSONL runs + `search_trajectories` MCP tool, LLM or RAG mode). Off by default; enable with `trajectory_enabled = true` (and `trajectory_retrieval_mode`) in `[agent.cli_agent]`. Set `trajectory_record_findings = true` to expose a `record_findings` tool so the agent emits a structured digest (situation/tell/root_cause/fix) that retrieval ranks on instead of raw tool mechanics. Design: [`docs/cli-agent-trajectory.md`](../docs/cli-agent-trajectory.md).
- `agents.yaml` — Agent registry consumed by the SRE Gym runner
