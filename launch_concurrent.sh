#!/bin/bash
# Launch opt2 and opt3 concurrently with version backup protection

cd /mnt/nvme1/khoav/Research/sds

echo "=== Starting Concurrent Experiments ==="
echo "Start time: $(date)"

# Backup current optimized prompts
echo "Backing up current prompts..."
cp -r app_operator/prompts/optimized app_operator/prompts/optimized_pre_opt23_$(date +%Y%m%d_%H%M%S)

# Start opt2
echo "Starting opt2..."
./start_opt2.sh &
OPT2_PID=$!
echo "opt2 PID: $OPT2_PID"

# Small delay to let opt2 initialize
sleep 5

# Start opt3
echo "Starting opt3..."
./start_opt3.sh &
OPT3_PID=$!
echo "opt3 PID: $OPT3_PID"

echo ""
echo "=== Both experiments running ==="
echo "opt2 PID: $OPT2_PID (log: opt2_run.log)"
echo "opt3 PID: $OPT3_PID (log: opt3_run.log)"
echo ""
echo "Monitor with:"
echo "  tail -f opt2_run.log"
echo "  tail -f opt3_run.log"
echo "  watch -n 60 'ps aux | grep -E \"opt2|opt3\" | grep -v grep'"
echo ""
echo "Version backups will be saved periodically..."

# Background version backup loop
(
  while ps -p $OPT2_PID > /dev/null 2>&1 || ps -p $OPT3_PID > /dev/null 2>&1; do
    sleep 1800  # Every 30 minutes
    if [ -d app_operator/prompts/optimized ]; then
      TIMESTAMP=$(date +%Y%m%d_%H%M%S)
      echo "[$(date)] Backing up optimized prompts to optimized_backup_$TIMESTAMP"
      cp -r app_operator/prompts/optimized "app_operator/prompts/optimized_backup_$TIMESTAMP"
    fi
  done
) &

BACKUP_PID=$!

# Wait for both to complete
wait $OPT2_PID
OPT2_EXIT=$?
echo "opt2 completed with exit code: $OPT2_EXIT at $(date)"

wait $OPT3_PID
OPT3_EXIT=$?
echo "opt3 completed with exit code: $OPT3_EXIT at $(date)"

# Stop backup loop
kill $BACKUP_PID 2>/dev/null

# Final backup
echo "Creating final backup..."
cp -r app_operator/prompts/optimized "app_operator/prompts/optimized_final_$(date +%Y%m%d_%H%M%S)"

echo ""
echo "=== Experiments Complete ==="
echo "opt2 exit code: $OPT2_EXIT"
echo "opt3 exit code: $OPT3_EXIT"
echo "End time: $(date)"
echo ""
echo "View results:"
echo "  uv run python scripts/visualize_e2e_results.py --work-dir opt2 --output opt2_results.png"
echo "  uv run python scripts/visualize_e2e_results.py --work-dir opt3 --output opt3_results.png"
