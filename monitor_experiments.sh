#!/bin/bash
# Monitor opt2 and opt3 progress

cd /mnt/nvme1/khoav/Research/sds

clear
echo "=== Experiment Monitor ==="
echo "Press Ctrl+C to exit"
echo ""

while true; do
  clear
  echo "=== Concurrent Experiments Monitor ==="
  echo "Updated: $(date)"
  echo ""

  # Check if processes are running
  echo "--- Process Status ---"
  if ps aux | grep -E "opt2.*e2e-optimize" | grep -v grep > /dev/null; then
    echo "✓ opt2: RUNNING"
  else
    echo "✗ opt2: NOT RUNNING"
  fi

  if ps aux | grep -E "opt3.*e2e-optimize" | grep -v grep > /dev/null; then
    echo "✓ opt3: RUNNING"
  else
    echo "✗ opt3: NOT RUNNING"
  fi
  echo ""

  # Show state
  echo "--- opt2 State ---"
  if [ -f opt2/state.json ]; then
    cat opt2/state.json | jq -r '"Iteration: \(.current_iteration), Version: \(.current_version // "none"), Completed training: \(.completed_train_apps | join(", "))"' 2>/dev/null || echo "State file exists but couldn't parse"
  else
    echo "No state file yet"
  fi
  echo ""

  echo "--- opt3 State ---"
  if [ -f opt3/state.json ]; then
    cat opt3/state.json | jq -r '"Iteration: \(.current_iteration), Version: \(.current_version // "none"), Completed training: \(.completed_train_apps | join(", "))"' 2>/dev/null || echo "State file exists but couldn't parse"
  else
    echo "No state file yet"
  fi
  echo ""

  # Show work directories
  echo "--- Experiment Directories ---"
  echo "opt2: $(ls -d opt2/*_iter*/ 2>/dev/null | wc -l) runs"
  echo "opt3: $(ls -d opt3/*_iter*/ 2>/dev/null | wc -l) runs"
  echo ""

  # Show recent log lines
  echo "--- Recent Activity ---"
  echo "opt2: $(tail -1 opt2_run.log 2>/dev/null | cut -c 1-80)"
  echo "opt3: $(tail -1 opt3_run.log 2>/dev/null | cut -c 1-80)"
  echo ""

  echo "--- Resource Usage ---"
  ps aux | grep -E "opt2|opt3|gemini" | grep -v grep | grep -v monitor | awk '{print $11, "CPU:", $3"%", "MEM:", $4"%"}' | head -5
  echo ""

  echo "Commands:"
  echo "  tail -f opt2_run.log  # Follow opt2 logs"
  echo "  tail -f opt3_run.log  # Follow opt3 logs"

  sleep 30
done
