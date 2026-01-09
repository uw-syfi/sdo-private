#!/bin/bash
# Format Python code using autopep8

set -e

# Get the directory of the script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Formatting code in project..."
cd "$PROJECT_ROOT"

# Run autopep8 via uv
# -i: in-place
# -r: recursive
# -a: aggressive (level 1)
# Using --extra test to ensure autopep8 is available
uv run --extra test autopep8 -i -r -a app_operator tools tests

echo "Formatting complete."