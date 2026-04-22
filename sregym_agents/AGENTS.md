# SRE Gym Agents

Agents that compete in the SRE Gym environment (fault diagnosis and remediation benchmarks).

## Agents

Registered in `agents.yaml`. Each agent has a `kickoff_command` run from the repo root.

- **crucible** — `uv run python -m sregym_agents.crucible.driver`
- **cli_agent** — `uv run python -m sregym_agents.cli_agent` (minimal `agentshim` wrapper)

## Architecture

- `crucible/` — Crucible-environment agent (pydantic-ai: orchestrator, judge, SRE agent, tools)
- `cli_agent/` — Thin wrapper around `agentshim` CLI agents (claude / codex / gemini / opencode); single-shot per stage
- `agents.yaml` — Agent registry consumed by the SRE Gym runner
