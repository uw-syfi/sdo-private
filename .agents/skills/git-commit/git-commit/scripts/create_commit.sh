#!/bin/bash
# Git commit automation script
# Usage: create_commit.sh <message_file> [files_to_stage...]

set -euo pipefail

MESSAGE_FILE="$1"
shift

# Stage files if provided, otherwise use what's already staged
if [ $# -gt 0 ]; then
    git add "$@"
fi

# Create commit with message from file
git commit -F "$MESSAGE_FILE"
