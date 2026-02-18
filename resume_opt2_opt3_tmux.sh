#!/bin/bash
# Resume opt2 and opt3 experiments in tmux

cd /mnt/nvme1/khoav/Research/sds

echo "=========================================="
echo "  Resuming opt2 & opt3 in tmux"
echo "=========================================="
echo "Start time: $(date)"
echo ""

# Check if tmux sessions already exist
if tmux has-session -t opt2 2>/dev/null; then
    echo "⚠️  tmux session 'opt2' already exists!"
    read -p "Kill and restart? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        tmux kill-session -t opt2
    else
        echo "Keeping existing session. Attach with: tmux attach -t opt2"
    fi
fi

if tmux has-session -t opt3 2>/dev/null; then
    echo "⚠️  tmux session 'opt3' already exists!"
    read -p "Kill and restart? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        tmux kill-session -t opt3
    else
        echo "Keeping existing session. Attach with: tmux attach -t opt3"
    fi
fi

# Start opt2 in tmux
echo "[$(date +%H:%M:%S)] Starting opt2 in tmux..."
tmux new-session -d -s opt2 "cd /mnt/nvme1/khoav/Research/sds && ./start_opt2.sh"
echo "  ✅ opt2 running in tmux session 'opt2'"

# Small delay to stagger API calls
sleep 10

# Start opt3 in tmux
echo "[$(date +%H:%M:%S)] Starting opt3 in tmux..."
tmux new-session -d -s opt3 "cd /mnt/nvme1/khoav/Research/sds && ./start_opt3.sh"
echo "  ✅ opt3 running in tmux session 'opt3'"

echo ""
echo "=========================================="
echo "  Both Experiments Running in tmux"
echo "=========================================="
echo ""
echo "📊 Monitor progress:"
echo "  tmux attach -t opt2     # Attach to opt2"
echo "  tmux attach -t opt3     # Attach to opt3"
echo "  tail -f opt2_run.log    # Follow opt2 log"
echo "  tail -f opt3_run.log    # Follow opt3 log"
echo "  ./monitor_experiments.sh # Dashboard (if available)"
echo ""
echo "📋 tmux commands (when attached):"
echo "  Ctrl+B, D  - Detach from session"
echo "  Ctrl+C     - Stop experiment"
echo ""
echo "⏱️  Expected timeline:"
echo "  Iteration 1 optimization: ~1 hour each"
echo "  Iteration 2 (train+opt):  ~2 hours each"
echo "  Iteration 3 (train+opt):  ~2 hours each"
echo "  Total remaining: ~5-6 hours per experiment"
echo ""
echo "🎯 What will happen:"
echo "  1. Skip iteration 1 training (already done ✅)"
echo "  2. Run iteration 1 optimization (~1 hour)"
echo "  3. Continue with iterations 2-3 normally"
echo ""
echo "🔍 Check status:"
echo "  tmux ls                 # List all sessions"
echo "  cat opt2/state.json     # Check opt2 progress"
echo "  cat opt3/state.json     # Check opt3 progress"
echo ""
