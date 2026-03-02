#!/usr/bin/env bash
# Run all RLM comparison experiments sequentially.
# Training set: rlm, subagent, hybrid on hotelReservation + socialNetwork
# Validation set: rlm, subagent, hybrid on fleetcast
set -euo pipefail

cd "$(dirname "$0")/.."

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
