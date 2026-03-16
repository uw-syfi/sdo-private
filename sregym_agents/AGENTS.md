# SRE Gym Agents

Agents that compete in the SRE Gym environment (fault diagnosis and remediation benchmarks).

## Agents

Registered in `agents.yaml`. Each agent has a `kickoff_command` run from the repo root.

- **pydantic_agent** — `uv run python -m sregym_agents.pydantic_agent.driver`
- **crucible** — `uv run python -m sregym_agents.crucible.driver`

## Architecture

- `crucible/` — Crucible-environment agent (orchestrator, judge, SRE agent, tools)
- `pydantic_agent/` — Pydantic-AI based agent
- `agents.yaml` — Agent registry consumed by the SRE Gym runner
