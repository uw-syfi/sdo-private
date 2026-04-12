#!/bin/bash
set -e

# Get repo root
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Start Backend
echo "Starting LegoAgent Backend on port 8000..."
cd "$REPO_ROOT"
# Use uvicorn with --reload-dir to only watch lego_agent source files.
# Without this, uvicorn detects generated_script.py in lego_agent_runs/ and
# restarts the server mid-execution, closing the WebSocket before the script runs.
uv run uvicorn lego_agent.server:app --reload --reload-dir lego_agent --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Start Frontend
echo "Starting LegoAgent Frontend on port 3000..."
cd "$REPO_ROOT/lego_agent/ui"
npm run dev &
FRONTEND_PID=$!

# Cleanup on exit
trap "kill $BACKEND_PID $FRONTEND_PID" EXIT

echo "Access the UI at http://localhost:3000"
echo ""
echo "NOTE: If you are running over SSH, ensure you have forwarded BOTH ports:"
echo "  ssh -L 3000:localhost:3000 -L 8000:localhost:8000 user@host"
wait
