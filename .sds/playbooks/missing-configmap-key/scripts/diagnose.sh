#!/usr/bin/env bash
# diagnose.sh — Detect services crashing due to a missing/blank required key in their ConfigMap.
# Usage: bash diagnose.sh [namespace]
set -euo pipefail

NS="${1:-hotel-reservation}"

echo "=== Checking for pods in Error/CrashLoopBackOff ==="
CRASHING=$(kubectl get pods -n "$NS" --no-headers | awk '$3 ~ /Error|CrashLoopBackOff|OOMKilled/ {print $1}')

if [[ -z "$CRASHING" ]]; then
  echo "No crashing pods found."
  exit 0
fi

for POD in $CRASHING; do
  echo ""
  echo "--- Pod: $POD ---"

  # Check logs for empty-value pattern
  LOGS=$(kubectl logs -n "$NS" "$POD" 2>/dev/null || kubectl logs -n "$NS" "$POD" --previous 2>/dev/null || true)

  if echo "$LOGS" | grep -qE "Read (database URL|.*address):\s*$"; then
    echo "DETECTED: Empty config value read — likely missing ConfigMap key"
    echo "Log evidence:"
    echo "$LOGS" | grep -E "Read (database URL|.*address):|no reachable servers|panic" | head -5

    # Identify the ConfigMap name from pod volumes
    CM=$(kubectl get pod -n "$NS" "$POD" -o jsonpath='{.spec.volumes[*].configMap.name}' 2>/dev/null || true)
    echo "ConfigMap(s) referenced by this pod: $CM"

    for C in $CM; do
      echo ""
      echo "Contents of ConfigMap '$C':"
      kubectl get configmap "$C" -n "$NS" -o jsonpath='{.data}' 2>/dev/null | python3 -m json.tool 2>/dev/null || \
        kubectl get configmap "$C" -n "$NS" -o yaml 2>/dev/null | grep -A20 "^data:"
    done
  else
    echo "No empty-config-value pattern detected in logs. Showing last 5 lines:"
    echo "$LOGS" | tail -5
  fi
done

echo ""
echo "=== Diagnosis complete ==="
