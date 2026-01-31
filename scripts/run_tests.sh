#!/bin/bash
# Run tests using pytest via uv

set -e

# Get the directory of the script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Running tests..."
cd "$PROJECT_ROOT"

PYTEST_ARGS=()
COVERAGE=false

for arg in "$@"; do
    if [ "$arg" == "--cov" ]; then
        COVERAGE=true
    else
        PYTEST_ARGS+=("$arg")
    fi
done

if [ "$COVERAGE" = true ]; then
    # Run with coverage for main packages
    uv run --extra test pytest "${PYTEST_ARGS[@]}" --cov=app_operator --cov=agentflow --cov-report=term-missing tests
else
    # Run standard pytest
    uv run --extra test pytest "${PYTEST_ARGS[@]}" tests
fi
