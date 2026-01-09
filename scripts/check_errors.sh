#!/bin/bash
# Check for static errors using ruff (via uv)
# This covers:
# - Syntax errors (including malformed indents)
# - Missing imports / Undefined names (F821, F403, F405)
# - Unused imports/variables (F401, F841)

set -e

# Get the directory of the script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Running static analysis with ruff..."
cd "$PROJECT_ROOT"

# Use 'uv tool run' to execute ruff without installing it as a project dependency
if command -v uv >/dev/null 2>&1; then
    if [ "$1" == "--fix" ]; then
        echo "Auto-fixing errors..."
        uv tool run ruff check --fix --exclude apps/ .
    else
        uv tool run ruff check --exclude apps/ .
    fi
else
    echo "Error: 'uv' is not installed. Please install uv to run this script."
    exit 1
fi
