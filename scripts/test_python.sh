#!/bin/bash
# Run python tests using pytest via uv

set -e

# Get the directory of the script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Running Python tests..."
cd "$PROJECT_ROOT"

# Run pytest with coverage
uv run --extra test pytest --cov --cov-report=term-missing "$@" tests
