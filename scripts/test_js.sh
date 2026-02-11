#!/bin/bash
# Run frontend tests

set -e

# Get the directory of the script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Run frontend tests
if [ -d "$PROJECT_ROOT/lego_agent/ui" ]; then
    echo "Running frontend tests..."
    cd "$PROJECT_ROOT/lego_agent/ui"
    # Install dependencies if node_modules is missing or if running in CI
    if [ -n "$CI" ] || [ ! -d "node_modules" ]; then
        echo "Installing frontend dependencies..."
        npm ci
    fi
    npm test
else
    echo "Frontend directory not found at $PROJECT_ROOT/lego_agent/ui"
    exit 1
fi
