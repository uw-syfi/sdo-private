#!/bin/bash
# Quick status check for opt2 and opt3 experiments

cd /mnt/nvme1/khoav/Research/sds

echo "=========================================="
echo "  Experiment Status Check"
echo "=========================================="
echo "Time: $(date)"
echo ""

# Function to get status from state.json
get_status() {
    local exp=$1
    local state_file="${exp}/state.json"

    if [ ! -f "$state_file" ]; then
        echo "  ❌ No state file found"
        return
    fi

    local current_iter=$(jq -r '.current_iteration' "$state_file")
    local completed_train=$(jq -r '.completed_train_apps | length' "$state_file")
    local opt_done=$(jq -r '.optimization_done' "$state_file")
    local current_ver=$(jq -r '.current_version // "none"' "$state_file")
    local completed_val=$(jq -r '.completed_val_apps | length' "$state_file")

    echo "  Current iteration: $current_iter"
    echo "  Training apps completed: $completed_train"
    echo "  Optimization done: $opt_done"
    echo "  Current version: $current_ver"
    echo "  Validation apps completed: $completed_val"
}

# Check opt2
echo "📊 opt2 (train-ticket in training):"
get_status "opt2"

# Check if tmux session is running
if tmux has-session -t opt2 2>/dev/null; then
    echo "  ✅ tmux session 'opt2' is RUNNING"
else
    echo "  ⏸️  tmux session 'opt2' is NOT running"
fi

# Check recent log activity
if [ -f opt2_run.log ]; then
    last_log=$(tail -1 opt2_run.log 2>/dev/null)
    echo "  Last log: ${last_log:0:80}..."
fi

echo ""

# Check opt3
echo "📊 opt3 (train-ticket in validation):"
get_status "opt3"

# Check if tmux session is running
if tmux has-session -t opt3 2>/dev/null; then
    echo "  ✅ tmux session 'opt3' is RUNNING"
else
    echo "  ⏸️  tmux session 'opt3' is NOT running"
fi

# Check recent log activity
if [ -f opt3_run.log ]; then
    last_log=$(tail -1 opt3_run.log 2>/dev/null)
    echo "  Last log: ${last_log:0:80}..."
fi

echo ""
echo "=========================================="
echo ""
echo "📋 Quick commands:"
echo "  tmux attach -t opt2        # View opt2 live"
echo "  tmux attach -t opt3        # View opt3 live"
echo "  tail -f opt2_run.log       # Follow opt2 log"
echo "  tail -f opt3_run.log       # Follow opt3 log"
echo ""
