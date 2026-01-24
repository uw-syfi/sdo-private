#!/bin/bash
# Create and checkout a git branch following the format: <name>/<type>/<brief-desc>

set -e

# Function to extract short name from git user.name
get_short_name() {
    local full_name="$1"
    # Extract first part before hyphen or space, convert to lowercase
    echo "$full_name" | awk -F'[-[:space:]]' '{print tolower($1)}'
}

# Parse arguments
if [ $# -lt 2 ]; then
    echo "Usage: $0 <type> <brief-desc> [name]"
    echo "  type: feat, fix, refactor, chore, etc."
    echo "  brief-desc: lowercase description with dashes"
    echo "  name: (optional) developer name. If not provided, inferred from git user.name"
    exit 1
fi

TYPE="$1"
DESC="$2"
NAME="${3:-}"

# Get name from git config if not provided
if [ -z "$NAME" ]; then
    GIT_USER=$(git config user.name)
    if [ -z "$GIT_USER" ]; then
        echo "Error: Could not get git user.name. Please set it with: git config user.name 'Your Name'"
        exit 1
    fi
    NAME=$(get_short_name "$GIT_USER")
fi

# Construct branch name
BRANCH_NAME="${NAME}/${TYPE}/${DESC}"

# Validate branch name format (basic check)
if [[ ! "$BRANCH_NAME" =~ ^[a-z0-9-]+/[a-z0-9-]+/[a-z0-9-]+$ ]]; then
    echo "Warning: Branch name '$BRANCH_NAME' may not follow the expected format"
    echo "Expected: lowercase letters, numbers, and dashes only"
fi

# Create and checkout the branch
echo "Creating and checking out branch: $BRANCH_NAME"
git checkout -b "$BRANCH_NAME"

echo "✅ Successfully created and checked out branch: $BRANCH_NAME"
