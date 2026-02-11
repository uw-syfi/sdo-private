#!/bin/bash
# Run tests using pytest via uv and npm for frontend

set -e

# Get the directory of the script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "Running all tests..."

# Run Python tests
"$SCRIPT_DIR/test_python.sh" "$@"

# Run frontend tests
"$SCRIPT_DIR/test_js.sh"
