#!/bin/bash
# Post-edit hook for Cursor that runs code formatting and error checking.
# This hook is called after file edits to ensure code quality.

set -e

# Get the project root (parent of .cursor directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SCRIPTS_DIR="$PROJECT_ROOT/scripts"

cd "$PROJECT_ROOT"

# Format code first
echo "Formatting code..."
bash "$SCRIPTS_DIR/format_code.sh"

# Then check for errors (with --fix to auto-fix what we can)
echo "Checking for errors..."
bash "$SCRIPTS_DIR/check_errors.sh" --fix

echo "Post-edit checks complete."
