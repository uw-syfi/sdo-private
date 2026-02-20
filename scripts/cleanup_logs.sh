#!/bin/bash
# Cleanup script to remove all deployment logs while keeping scripts

if [ -z "$1" ]; then
    echo "Usage: $0 <path-to-repo>"
    echo "Example: $0 /Users/noahhoang/Development/SDS_Research/SDS/exp/hotelReservation/hotel-exp"
    exit 1
fi

REPO_PATH="$1"
SDS_DIR="${REPO_PATH}/.sds"

if [ ! -d "$SDS_DIR" ]; then
    echo "Error: .sds directory not found at $SDS_DIR"
    exit 1
fi

echo "Cleaning up logs in $SDS_DIR..."

# Remove all log files
if [ -d "$SDS_DIR/logs" ]; then
    echo "  Removing logs directory..."
    rm -rf "$SDS_DIR/logs"
    mkdir -p "$SDS_DIR/logs"
    echo "  ✓ Logs directory cleared"
fi

# Remove fix_summary.md
if [ -f "$SDS_DIR/fix_summary.md" ]; then
    rm -f "$SDS_DIR/fix_summary.md"
    echo "  ✓ Removed fix_summary.md"
fi

# Remove trajectory files
if [ -d "$SDS_DIR/trajectories" ]; then
    echo "  Removing trajectory files..."
    rm -rf "$SDS_DIR/trajectories"
    echo "  ✓ Trajectories directory removed"
fi

if [ -f "$SDS_DIR/trajectory.json" ] || [ -L "$SDS_DIR/trajectory.json" ]; then
    rm -f "$SDS_DIR/trajectory.json"
    echo "  ✓ Removed trajectory.json symlink"
fi

echo ""
echo "✓ Cleanup complete! Scripts (deploy.sh, health_check.sh) and analysis files are preserved."
echo "  Next deployment will start from attempt #1."

