#!/bin/bash
# Run tests using pytest via uv

set -e

# Get the directory of the script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Running tests..."
cd "$PROJECT_ROOT"

# Run pytest via uv
# Using --extra test to ensure pytest is available
uv run --extra test pytest tests
