#!/bin/bash
# Run an SREGym experiment.
#
# Usage:
#   bash scripts/run_sregym.sh                                     # new run (default config)
#   bash scripts/run_sregym.sh sregym_agents/experiments/default.toml  # new run
#   bash scripts/run_sregym.sh bench/sregym/logs/<exp-dir>/        # resume
#
# Environment variable overrides (e.g. MODEL=foo) are still supported.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

if [ $# -eq 0 ]; then
  exec uv run python scripts/run_sregym.py sregym_agents/experiments/default.toml
else
  exec uv run python scripts/run_sregym.py "$@"
fi
