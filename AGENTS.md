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
- **Config**: dataclasses with `__post_init__` validation (`TypeError`/`ValueError`)
- **Exceptions**: custom hierarchy in `app_operator/exceptions.py`
- **Adding a new feature**: think about tests first. Test public behavior, not internal details.
- **Fixing bugs**: write a reproducing test first, then fix. Test must be part of the fix.

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
