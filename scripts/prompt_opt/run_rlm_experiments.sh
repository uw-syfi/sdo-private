#!/usr/bin/env bash
# Run all RLM comparison experiments sequentially.
# Training set: rlm, subagent, hybrid on hotelReservation + socialNetwork
# Validation set: rlm, subagent, hybrid on fleetcast
#
# Usage:
#   bash scripts/run_rlm_experiments.sh        # launch in a new tmux session
#   bash scripts/run_rlm_experiments.sh --run  # run directly (used internally by tmux)
set -euo pipefail

SESSION="rlm-exp"
SCRIPT="$(realpath "$0")"
REPO="$(dirname "$SCRIPT")/../.."

# If not inside the tmux session yet, create one and re-invoke this script with --run.
if [[ "${1:-}" != "--run" ]]; then
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        echo "Session '$SESSION' already exists. Attaching..."
        tmux attach -t "$SESSION"
        exit 0
    fi
    tmux new-session -d -s "$SESSION" -c "$(realpath "$REPO")"
    tmux send-keys -t "$SESSION" \
        "bash $SCRIPT --run 2>&1 | tee $(realpath "$REPO")/exp_config/rlm_experiments.log" Enter
    echo "Launched in tmux session '$SESSION'. Attaching..."
    tmux attach -t "$SESSION"
    exit 0
fi

# -- Actual experiment runner (runs inside tmux) ------------------------------
cd "$REPO"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

EXPERIMENTS=(
    rlm-train
    subagent-train
    hybrid-train
    rlm-val
    subagent-val
    hybrid-val
)

TOTAL=${#EXPERIMENTS[@]}
log "Starting $TOTAL experiments sequentially"

for i in "${!EXPERIMENTS[@]}"; do
    exp="${EXPERIMENTS[$i]}"
    log "=== [$((i+1))/$TOTAL] $exp ==="
    uv run -m app_operator run-exp "$exp"
    log "=== [$((i+1))/$TOTAL] $exp DONE ==="
done

log "All experiments complete."
