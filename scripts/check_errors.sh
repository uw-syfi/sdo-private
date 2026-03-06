#!/bin/bash
# Check for static errors.  Covers:
#   ruff        — syntax, undefined names, unused imports, style
#   lint-imports — architectural layer contracts (pyproject.toml [tool.importlinter])

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: 'uv' is not installed."
    exit 1
fi

echo "==> ruff"
if [ "$1" == "--fix" ]; then
    uv run ruff check --fix .
else
    uv run ruff check .
fi

echo "==> import-linter (layer contracts)"
uv run lint-imports
