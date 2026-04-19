# SDS (Self-Defining Systems)

SDS autonomously explores, validates, and evolves infrastructure using agentic LLMs.

## Projects

- [`app_operator/`](app_operator/AGENTS.md) — Deploy + monitor target apps with LLM agents
- [`lego_agent/`](lego_agent/AGENTS.md) — Autonomous script generation (Web UI + TUI + CLI)
- [`sregym_agents/`](sregym_agents/AGENTS.md) — SRE Gym competition agents
- `libs/` — Shared libraries: `agent_cli`, `agent_mw`, `model_config`, `pydantic_agent`, `sds_core`
- `apps/` — Target applications (hotelReservation, socialNetwork, ...)
- `tests/` — `unit/`, `integration/`

## Development Conventions

- **Test-driven**: begin designing your work by thinking about how to test it. Test suite is part of your plan.
- **Red/Green TDD**: write failing tests first, then implement.
- **Python**: type hints, `autopep8` formatting, `ruff` linting, `pytest` tests
- **Structured data**: use dataclasses instead of raw `dict` for shapes constructed/consumed in multiple places
- **Config**: prefer Pydantic v2 for new config classes — `BaseModel` with `ConfigDict(extra="forbid")` when no field name collides with `model_config`, otherwise `pydantic.dataclasses.dataclass`. Use `@field_validator` / `@model_validator` for constraints and cross-field rules. Plain data containers (no validation) stay as `@dataclass`. `app_operator/core/config.py` still uses `@dataclass` + `__post_init__` + helpers in `app_operator/core/validation.py` for historical reasons (extensive tests assert specific `TypeError`/`ValueError` contracts); leave as-is unless migrating the tests too.
- **Exceptions**: custom hierarchy in `app_operator/exceptions.py`
- **Adding a new feature**: Think about tests first. Test public behavior, not internal details.
- **Fixing bugs**: Write a reproducing test first, then fix. Test must be part of the fix.

## Usage Guide for LLM Agents

- **Debugging deployment**: Check `.sds/deploy.sh` and `.sds/logs/`
- **Debugging lego_agent**: Check `lego_agent_runs/<timestamp>/lego_agent.py`
- **Optimizing prompts**: `analyze-prompts` baseline → `optimize-prompts --dry-run` → compare
- **Adding DSPy signatures**: Add to `dspy_integration/signatures.py`, register in `SIGNATURES` dict
- **Extending metrics**: Modify `dspy_integration/metrics.py`, ensure weights sum to 1.0
- **Adding fault types**: Add to `COMPOSE_FAULTS`, implement `_inject_*`, register in dispatch table, add tests

## Remote repo access

Use `glab` command (if available) to access the remote repo on GitLab, including issues and merge requests.

## Notes from Developers

- Use `uv` and `uv run ...` for Python.
- Keep this file concise — detailed docs live in `docs/`.
- When code changes impact CLI, update README.md.
- When removing code, delete it — do not comment it out.
- **Avoid nested event loops:** Code that already runs inside an async event loop (e.g. a pydantic-ai tool handler) must never call `run_sync()`, `asyncio.run()`, or `loop.run_until_complete()` — these create a second event loop, and any objects bound to the outer loop (httpx connection pools, anyio locks, etc.) will raise `RuntimeError: is bound to a different event loop`. Instead, make the function `async` and `await` the coroutine directly so it stays on the same loop.
- When changing trajectory format (`trajectory/`) or experiment log/result structures (`commands/run_exp.py`), update the `analyze-experiment` skill references in `.agents/skills/analyze-experiment/references/`.
- When adding or moving feature flags in `app_operator/config.py`, update `docs/feature-flags.md` to match.
- When introducing a new top-level project or library (e.g. `libs/`, `sregym_agents/`), add it to `tach.toml` with the correct `depends_on` entries.

## Code Validation (after every code edit)

```bash
bash scripts/format_code.sh   # autopep8 formatting
bash scripts/check_errors.sh  # ruff linting (add --fix to auto-fix)
```

## Testing

See [`docs/testing-guide.md`](docs/testing-guide.md) for full guidance.

```bash
scripts/run_tests.sh                      # all tests
uv run pytest tests/                      # python only
uv run pytest tests/ --cov=app_operator  # with coverage
```

## Notes

- Use `uv` and `uv run ...` for Python.
- Keep this file concise — detailed docs live in `docs/`.
- When code changes impact CLI, update README.md.
- When removing code, delete it — do not comment it out.
- Use `glab` command (if available) to access the remote repo on GitLab.
