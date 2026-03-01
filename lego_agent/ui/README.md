# lego_agent Web UI

This is the web frontend for `lego_agent`, the SDS agent workflow generator. It provides a chat-based interface for describing a task, iterating through clarification questions, and watching the generated workflow run.

## Starting the UI

The recommended way is the wrapper script from the repo root:

```bash
./scripts/start_lego_ui.sh
```

This starts both the backend server (`lego_agent/server.py`) and this Next.js frontend, then opens `http://localhost:3000`.

## Manual start (development)

If you need to run the frontend independently:

```bash
cd lego_agent/ui
npm install
npm run dev
```

The frontend expects the backend at `http://localhost:8000`. Start the backend separately:

```bash
uv run -m lego_agent.server
```

## What it does

1. Sends your task description to the backend
2. Displays the clarification loop (AI-generated questions to refine requirements)
3. Shows the generated agent workflow script
4. Streams execution output in real time

See `docs/lego-agent.md` for full documentation on the lego_agent system.
