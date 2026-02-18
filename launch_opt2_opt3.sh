#!/bin/bash
# Launch opt2 and opt3 concurrently (safe with separate output directories)

cd /mnt/nvme1/khoav/Research/sds

echo "=========================================="
echo "  Starting opt2 and opt3 Concurrently"
echo "=========================================="
echo "Start time: $(date)"
echo ""
echo "Configuration:"
echo "  opt2: Training with train-ticket → optimized/opt2/"
echo "  opt3: Validation with train-ticket → optimized/opt3/"
echo ""

# Start opt2
echo "[$(date +%H:%M:%S)] Starting opt2..."
./start_opt2.sh &
OPT2_PID=$!
echo "  opt2 PID: $OPT2_PID"
echo "  opt2 log: opt2_run.log"

# Start opt3 (small delay to stagger API calls)
sleep 10
echo "[$(date +%H:%M:%S)] Starting opt3..."
./start_opt3.sh &
OPT3_PID=$!
echo "  opt3 PID: $OPT3_PID"
echo "  opt3 log: opt3_run.log"

echo ""
echo "=========================================="
echo "  Both Experiments Running"
echo "=========================================="
echo "Monitor with:"
echo "  ./monitor_experiments.sh     # Live dashboard"
echo "  tail -f opt2_run.log         # Follow opt2"
echo "  tail -f opt3_run.log         # Follow opt3"
echo ""
echo "Expected completion: ~12-15 hours (overnight)"
echo ""
echo "Stop both:"
echo "  kill $OPT2_PID $OPT3_PID"
echo ""

# Optional: wait for completion
read -p "Press Enter to wait for completion (or Ctrl+C to detach)..."
echo "Waiting for experiments to complete..."

wait $OPT2_PID
OPT2_EXIT=$?
echo "[$(date)] opt2 completed with exit code: $OPT2_EXIT"

wait $OPT3_PID
OPT3_EXIT=$?
echo "[$(date)] opt3 completed with exit code: $OPT3_EXIT"

echo ""
echo "=========================================="
echo "  Experiments Complete!"
echo "=========================================="
echo "View results:"
echo "  uv run python scripts/visualize_e2e_results.py --work-dir opt2 --output opt2_results.png"
echo "  uv run python scripts/visualize_e2e_results.py --work-dir opt3 --output opt3_results.png"
echo ""
echo "Compare prompts:"
echo "  ls -lh app_operator/prompts/optimized/opt2/"
echo "  ls -lh app_operator/prompts/optimized/opt3/"
