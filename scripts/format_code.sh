#!/bin/bash
# Format Python code using autopep8

set -e

# Get the directory of the script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Formatting code in project..."
cd "$PROJECT_ROOT"

# Check for --check flag
if [ "$1" == "--check" ]; then
    echo "Checking code formatting (dry-run)..."
    MODE="--diff --exit-code"
else
    echo "Formatting code in project..."
    MODE="-i"
fi

# Run autopep8 via uv
# -i: in-place (replaced by MODE if --check is used)
# -r: recursive
# -a: aggressive (level 1)
# Using --extra test to ensure autopep8 is available
uv run autopep8 --in-place --recursive --max-line-length 120 app_operator lego_agent libs tests

echo "Formatting check complete."