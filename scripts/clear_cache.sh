#!/bin/bash
# Clear Python bytecode cache for all project packages.
# Run this if you see stale AttributeError or ModuleNotFoundError after
# editing source files (e.g. 'OperatorConfig has no attribute phase').

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Clearing Python bytecode cache..."
count=$(find "$REPO_ROOT/sdo" "$REPO_ROOT/benchmarks" "$REPO_ROOT/sregym_agents" "$REPO_ROOT/libs" -name "*.pyc" -delete -print | wc -l | tr -d ' ')
find "$REPO_ROOT/sdo" "$REPO_ROOT/benchmarks" "$REPO_ROOT/sregym_agents" "$REPO_ROOT/libs" -name "__pycache__" -type d -empty -delete
echo "Deleted $count .pyc files."
