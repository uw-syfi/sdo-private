#!/bin/bash
# Check architecture rules using import-linter
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Checking architecture rules..."
cd "$PROJECT_ROOT"

if command -v uv >/dev/null 2>&1; then
    uv run lint-imports
else
    echo "Error: 'uv' is not installed."
    exit 1
fi
