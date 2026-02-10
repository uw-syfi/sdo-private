#!/bin/bash
# Safe wrapper for running SDS operator with fault injection
# Ensures cleanup happens even on Ctrl+C

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
NUM_FAULTS=2
FAULT_SEED=""
REPO_PATH=""

# Help message
show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS] REPO_PATH

Safe wrapper for running SDS operator with fault injection.
Automatically cleans up fault injection and containers on exit (including Ctrl+C).

Arguments:
    REPO_PATH           Path to the application repository

Options:
    -n, --num-faults N      Number of faults to inject (default: 2)
    -s, --seed SEED         Random seed for fault injection (optional)
    -h, --help              Show this help message

Examples:
    $(basename "$0") apps/deathstarbench/hotelReservation
    $(basename "$0") -n 3 -s 42 apps/deathstarbench/socialNetwork

EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -n|--num-faults)
            NUM_FAULTS="$2"
            shift 2
            ;;
        -s|--seed)
            FAULT_SEED="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        -*)
            echo -e "${RED}Error: Unknown option $1${NC}" >&2
            show_help
            exit 1
            ;;
        *)
            REPO_PATH="$1"
            shift
            ;;
    esac
done

# Validate repo path
if [[ -z "$REPO_PATH" ]]; then
    echo -e "${RED}Error: REPO_PATH is required${NC}" >&2
    show_help
    exit 1
fi

if [[ ! -d "$REPO_PATH" ]]; then
    echo -e "${RED}Error: Repository path does not exist: $REPO_PATH${NC}" >&2
    exit 1
fi

# Convert to absolute path
REPO_PATH=$(cd "$REPO_PATH" && pwd)

# Cleanup function
cleanup() {
    local exit_code=$?
    echo ""
    echo -e "${YELLOW}Cleaning up...${NC}"

    # Revert fault injection
    echo -e "${YELLOW}Reverting fault injection...${NC}"
    if uv run -m app_operator.fault_injection.cli revert --repo-path "$REPO_PATH" 2>/dev/null; then
        echo -e "${GREEN}✓ Fault injection reverted${NC}"
    else
        echo -e "${YELLOW}  No fault injection to revert (or already reverted)${NC}"
    fi

    # Stop containers
    echo -e "${YELLOW}Stopping Docker containers...${NC}"
    if [[ -f "$REPO_PATH/docker-compose.yml" ]]; then
        if docker compose -f "$REPO_PATH/docker-compose.yml" down 2>/dev/null; then
            echo -e "${GREEN}✓ Containers stopped${NC}"
        else
            echo -e "${YELLOW}  No containers to stop (or already stopped)${NC}"
        fi
    fi

    echo -e "${GREEN}Cleanup complete${NC}"
    exit $exit_code
}

# Register cleanup on EXIT (catches Ctrl+C, normal exit, errors)
trap cleanup EXIT INT TERM

# Build inject command
INJECT_CMD="uv run -m app_operator.fault_injection.cli inject --repo-path $REPO_PATH --num-faults $NUM_FAULTS"
if [[ -n "$FAULT_SEED" ]]; then
    INJECT_CMD="$INJECT_CMD --seed $FAULT_SEED"
fi

# Inject faults
echo -e "${YELLOW}Injecting faults into $REPO_PATH...${NC}"
echo -e "${YELLOW}Command: $INJECT_CMD${NC}"
if eval "$INJECT_CMD"; then
    echo -e "${GREEN}✓ Faults injected${NC}"
else
    echo -e "${RED}✗ Fault injection failed${NC}"
    exit 1
fi

# Run operator
echo ""
echo -e "${GREEN}Starting SDS operator...${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop (cleanup will happen automatically)${NC}"
echo ""

./sds_operator run "$REPO_PATH"
