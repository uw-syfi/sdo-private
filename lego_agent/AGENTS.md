# LegoAgent

Autonomous script generation agent with Web UI, TUI, and CLI interfaces.

See [`../docs/lego-agent.md`](../docs/lego-agent.md) for full details.

## Key Commands

```bash
../scripts/start_lego_ui.sh                    # Web UI (recommended)
uv run -m lego_agent                           # TUI mode
uv run -m lego_agent --no-tui --prompt "task" # CLI mode
```

## Architecture

- `backend/engine.py` — Core generation loop
- `backend/runtime.py` — Script execution runtime
- `backend/server.py` — Web UI backend
- `ui/` — Web and TUI frontends

## Debugging

Check `lego_agent_runs/<timestamp>/lego_agent.py` for generated scripts from past runs.
