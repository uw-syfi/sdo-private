#!/usr/bin/env bash
# Run E2E prompt-optimization experiments for provider comparison.
# Providers: rlm, subagent, hybrid
# Training apps: hotelReservation + socialNetwork
# Validation app: fleetcast
#
# Usage:
#   bash scripts/prompt_opt/run_rlm_experiments.sh        # launch in a new tmux session
#   bash scripts/prompt_opt/run_rlm_experiments.sh --run  # run directly (used internally by tmux)
set -euo pipefail

SESSION="rlm-e2e-exp"
SCRIPT="$(realpath "$0")"
REPO="$(dirname "$SCRIPT")/../.."
MASTER_LOG="$(realpath "$REPO")/exp_config/rlm_e2e_experiments.log"

# If not inside the tmux session yet, create one and re-invoke this script with --run.
if [[ "${1:-}" != "--run" ]]; then
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        echo "Session '$SESSION' already exists. Attaching..."
        tmux attach -t "$SESSION"
        exit 0
    fi
    tmux new-session -d -s "$SESSION" -c "$(realpath "$REPO")"
    tmux send-keys -t "$SESSION" \
        "bash $SCRIPT --run 2>&1 | tee $MASTER_LOG" Enter
    echo "Launched in tmux session '$SESSION'. Attaching..."
    tmux attach -t "$SESSION"
    exit 0
fi

# -- Actual experiment runner (runs inside tmux) ------------------------------
cd "$REPO"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

CONFIGS=(
    exp_config/subagent-e2e/config.toml
    exp_config/rlm-e2e/config.toml
    exp_config/hybrid-e2e/config.toml
)

REQUIRED_APPS=(
    apps/deathstarbench/hotelReservation
    apps/deathstarbench/socialNetwork
    apps/fleetcast
)

TOTAL=${#CONFIGS[@]}
log "Starting $TOTAL E2E optimization experiments sequentially"
FAILED_EXPERIMENTS=()

for app_path in "${REQUIRED_APPS[@]}"; do
    if [[ ! -d "$app_path" ]]; then
        log "Missing app directory: $app_path"
        log "Populate apps/ benchmarks first, then rerun."
        exit 1
    fi
done

for i in "${!CONFIGS[@]}"; do
    config_path="${CONFIGS[$i]}"
    if [[ ! -f "$config_path" ]]; then
        log "Missing config: $config_path"
        exit 1
    fi

    exp_name="$(basename "$(dirname "$config_path")")"
    exp_log="exp_config/$exp_name/e2e_optimize.log"

    log "=== [$((i+1))/$TOTAL] $exp_name ==="
    if uv run -m app_operator e2e-optimize --config "$config_path" 2>&1 | tee "$exp_log"; then
        log "=== [$((i+1))/$TOTAL] $exp_name DONE (success) ==="
    else
        status=$?
        FAILED_EXPERIMENTS+=("$exp_name (exit=$status)")
        log "=== [$((i+1))/$TOTAL] $exp_name FAILED (exit=$status), continuing ==="
    fi
done

if [[ ${#FAILED_EXPERIMENTS[@]} -gt 0 ]]; then
    log "All E2E optimization experiments completed with failures."
    for failed in "${FAILED_EXPERIMENTS[@]}"; do
        log "  - $failed"
    done
    exit 1
fi

log "All E2E optimization experiments complete (all success)."
