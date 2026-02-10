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
RUN_PYTHON=true
RUN_JS=true

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --cov)
            COVERAGE=true
            shift
            ;;
        --python-only)
            RUN_JS=false
            shift
            ;;
        --js-only)
            RUN_PYTHON=false
            shift
            ;;
        *)
            PYTEST_ARGS+=("$1")
            shift
            ;;
    esac
done

if [ "$RUN_PYTHON" = true ]; then
    if [ "$COVERAGE" = true ]; then
        # Run with coverage for main packages
        uv run pytest tests/unit/lego_agent
    else
        # Run standard pytest
        uv run --extra test pytest "${PYTEST_ARGS[@]}" tests
    fi
fi

# Run frontend tests
if [ "$RUN_JS" = true ] && [ -d "lego_agent/ui" ]; then
    echo "Running frontend tests..."
    cd lego_agent/ui
    if [ "$CI" = "true" ] && [ -f "package-lock.json" ]; then
        npm ci
    else
        npm install
    fi
    npm test
fi
