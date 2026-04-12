# SRE Gym Agents

Agents that compete in the SRE Gym environment (fault diagnosis and remediation benchmarks).

## Agents

Registered in `agents.yaml`. Each agent has a `kickoff_command` run from the repo root.

- **crucible** — `uv run python -m sregym_agents.crucible.driver`

## Architecture

- `crucible/` — Crucible-environment agent (pydantic-ai: orchestrator, judge, SRE agent, tools)
- `agents.yaml` — Agent registry consumed by the SRE Gym runner
