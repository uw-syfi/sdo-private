#!/bin/bash
# Kill orphaned SREGym processes left behind after the orchestrator exits.
#
# What it kills:
#   1. sregym_agents.crucible.driver processes (agent job workers)
#   2. third_party/sregym/main.py processes (the orchestrator itself, if any)
#
# Each driver is launched with start_new_session=True, so we kill the
# entire process group (PGID) to clean up child subprocesses too.
#
# Usage:
#   bash scripts/kill_sregym.sh          # dry-run (show what would be killed)
#   bash scripts/kill_sregym.sh --kill   # actually kill them

set -euo pipefail

KILL_MODE=false
if [[ "${1:-}" == "--kill" ]]; then
    KILL_MODE=true
fi

# Patterns that identify sregym processes we own.
# Note: forked worker processes appear as ".venv/bin/python3 main.py --agent ..."
# (not "third_party/sregym/main.py") because the cwd is changed before exec.
PATTERNS=(
    "sregym_agents\.crucible\.driver"
    "third_party/sregym/main\.py"
    "main\.py --agent .+ --experiment-dir .+third_party/sregym"
)

# Collect session leader PIDs (the /bin/sh wrappers that head each process group).
LEADER_PIDS=()

for pat in "${PATTERNS[@]}"; do
    while IFS= read -r pid; do
        [[ -n "$pid" ]] && LEADER_PIDS+=("$pid")
    done < <(pgrep -f "$pat" -u "$(id -u)" 2>/dev/null || true)
done

if [[ ${#LEADER_PIDS[@]} -eq 0 ]]; then
    echo "No orphaned SREGym processes found."
    exit 0
fi

# De-duplicate and sort
mapfile -t LEADER_PIDS < <(printf '%s\n' "${LEADER_PIDS[@]}" | sort -un)

echo "Found ${#LEADER_PIDS[@]} SREGym process(es):"
for pid in "${LEADER_PIDS[@]}"; do
    # Show the command line for context
    cmdline=$(ps -p "$pid" -o args= 2>/dev/null || echo "(already exited)")
    echo "  PID $pid: $cmdline"
done

if [[ "$KILL_MODE" == false ]]; then
    echo
    echo "Dry-run mode. Re-run with --kill to terminate these processes."
    exit 0
fi

echo
echo "Killing process groups..."
for pid in "${LEADER_PIDS[@]}"; do
    # Try to kill the entire process group first (negative PID = PGID).
    pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
    if [[ -n "$pgid" && "$pgid" != "0" ]]; then
        kill -- -"$pgid" 2>/dev/null && echo "  Killed PGID $pgid (leader PID $pid)" && continue
    fi
    # Fallback: kill just the PID
    kill "$pid" 2>/dev/null && echo "  Killed PID $pid" || echo "  PID $pid already exited"
done

# Give them a moment, then SIGKILL any survivors
sleep 2
for pid in "${LEADER_PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
        pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
        if [[ -n "$pgid" && "$pgid" != "0" ]]; then
            kill -9 -- -"$pgid" 2>/dev/null || true
        fi
        kill -9 "$pid" 2>/dev/null || true
        echo "  Force-killed PID $pid"
    fi
done

echo "Done."
