#!/bin/bash
# Run the SREGym live deploy/undeploy CLI from the repository root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../bench/sregym"

exec uv run python main.py "$@"
