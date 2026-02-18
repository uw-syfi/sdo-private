#!/bin/bash
# Launch opt1 continuation in a new tmux session

SESSION_NAME="opt1_continue"

# Check if session already exists
if tmux has-session -t $SESSION_NAME 2>/dev/null; then
    echo "Error: tmux session '$SESSION_NAME' already exists."
    echo "Attach with: tmux attach -t $SESSION_NAME"
    echo "Or kill it first: tmux kill-session -t $SESSION_NAME"
    exit 1
fi

# Backup current optimized prompts
echo "Backing up current prompts..."
cd /mnt/nvme1/khoav/Research/sds
cp -r app_operator/prompts/optimized "app_operator/prompts/optimized_pre_opt1_continue_$(date +%Y%m%d_%H%M%S)"

# Create new tmux session
echo "Creating tmux session: $SESSION_NAME"
tmux new-session -d -s $SESSION_NAME -c /mnt/nvme1/khoav/Research/sds

# Run the experiment in the tmux session
tmux send-keys -t $SESSION_NAME "./start_opt1_continue.sh" C-m

echo ""
echo "=== opt1 continuation started in tmux session ==="
echo "Session name: $SESSION_NAME"
echo "Log file: opt1_continue_run.log"
echo ""
echo "Attach to session:"
echo "  tmux attach -t $SESSION_NAME"
echo ""
echo "Monitor progress:"
echo "  tail -f opt1_continue_run.log"
echo ""
echo "Detach from session: Ctrl+b then d"
echo ""
