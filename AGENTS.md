# SDO (Self-Defining Operator)

SDO autonomously deploys and operates applications using source-grounded agents, independently validated Go detectors, a long-running Kubernetes controller, and repository-backed operational memory.

## Projects

- [`sdo/`](sdo/AGENTS.md) — Python agent runtime, operational memory, contracts, and controller installation
- `controller/` — detector SDK, execution core, long-running runtime, and controller builder
- [`benchmarks/sregym/`](benchmarks/sregym/AGENTS.md) — first-party SREGym adapters, protocol clients, runners, experiments, analysis, and legacy benchmark agents
- `third_party/sregym/` — external SREGym harness Git submodule
- `libs/` — shared agent and model libraries; `sdo_core` contains neutral runtime helpers
- `apps/` — source-deployment evaluation applications
- `tests/` — unit and integration tests

Production code must not import from SREGym packages. Benchmark adapters may import the production API. The production SDO agent core belongs under `sdo/`; `benchmarks/sregym/agents/crucible/` is a legacy benchmark agent, not an alternate production runtime.

## Development conventions

- Begin with a failing test for bugs and new public behavior.
- Test contracts and ownership boundaries, not private implementation details.
- Use Python type hints, `autopep8`, `ruff`, and `pytest`.
- Prefer dataclasses or Pydantic models over repeated raw mapping shapes.
- Validate configuration in `__post_init__` with `TypeError` or `ValueError`.
- Keep exceptions next to the owning SDO subsystem and preserve actionable context.
- Delete removed code instead of commenting it out.
- Cap fuzz and property-test runs (for example a Hypothesis fuzz profile) at 5 minutes of wall-clock time. Run longer only when the user explicitly asks for a long run.
- Keep code already running in an async loop on that loop: use `async`/`await`, never `run_sync()`, `asyncio.run()`, or `run_until_complete()`.

## SDO invariants

- `.sdo/` contains the five durable artifact classes: goal, architecture, playbooks, diagnostics, and outcomes.
- Artifact ownership is enforced by the commit broker. Agents propose changes in isolated worktrees; only validated commits reach the operational branch.
- The health judge owns health detectors. Responders may add or refine incident detectors and playbooks after independently verified outcomes.
- Detector runtime code is deterministic Go using `controller/sdk`; it must not call an LLM or inspect benchmark verdicts.
- `controller/runtime` remains transport-neutral. SREGym submission relays and receipts stay behind the benchmark adapter.

When changing operational-memory formats or experiment result structures, update `.agents/skills/analyze-experiment/references/`. When moving production or benchmark boundaries, update `docs/architecture.md` and the paper-scope matrix.

## Validation after code edits

```bash
bash scripts/format_code.sh
bash scripts/check_errors.sh
```

Use `uv` and `uv run ...` for Python. See [docs/testing-guide.md](docs/testing-guide.md) for focused Python and Go commands.

## Remote repository access

Use `glab` when available for GitLab issues and merge requests.
