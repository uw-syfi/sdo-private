# SDO (Self-Defining Operator)

SDO autonomously deploys and operates applications using source-grounded agents, independently validated Go detectors, a long-running Kubernetes controller, and repository-backed operational memory.

## Projects

- [`app_operator/`](app_operator/AGENTS.md) — lifecycle, operational memory, responder protocol, and Kubernetes runtime
- `controller/` — detector SDK, execution core, long-running runtime, and controller builder
- [`sregym_agents/`](sregym_agents/AGENTS.md) — SRE Gym benchmark agents
- `app_operator/sdo_sregym/`, `bench/sregym/`, `libs/sregym_lib/` — benchmark-only adapters and harnesses
- `libs/` — shared agent and model libraries; `sdo_core` contains neutral runtime helpers
- `apps/` — source-deployment evaluation applications
- `tests/` — unit and integration tests

Production code must not import from SRE Gym packages. Benchmark adapters may import the production API.

## Development conventions

- Begin with a failing test for bugs and new public behavior.
- Test contracts and ownership boundaries, not private implementation details.
- Use Python type hints, `autopep8`, `ruff`, and `pytest`.
- Prefer dataclasses or Pydantic models over repeated raw mapping shapes.
- Validate configuration in `__post_init__` with `TypeError` or `ValueError`.
- Keep exceptions in the `app_operator/exceptions.py` hierarchy where applicable.
- Delete removed code instead of commenting it out.
- Keep code already running in an async loop on that loop: use `async`/`await`, never `run_sync()`, `asyncio.run()`, or `run_until_complete()`.

## SDO invariants

- `.sdo/` contains the five durable artifact classes: goal, architecture, playbooks, diagnostics, and outcomes.
- Artifact ownership is enforced by the commit broker. Agents propose changes in isolated worktrees; only validated commits reach the operational branch.
- The health judge owns health detectors. Responders may add or refine incident detectors and playbooks after independently verified outcomes.
- Detector runtime code is deterministic Go using `controller/sdk`; it must not call an LLM or inspect benchmark verdicts.
- `controller/runtime` remains transport-neutral. SRE Gym submission relays and receipts stay behind the benchmark adapter.

When changing operational-memory formats or experiment result structures, update `.agents/skills/analyze-experiment/references/`. When moving production or benchmark boundaries, update `docs/architecture.md` and the paper-scope matrix.

## Validation after code edits

```bash
bash scripts/format_code.sh
bash scripts/check_errors.sh
```

Use `uv` and `uv run ...` for Python. See [docs/testing-guide.md](docs/testing-guide.md) for focused Python and Go commands.

## Remote repository access

Use `glab` when available for GitLab issues and merge requests.
