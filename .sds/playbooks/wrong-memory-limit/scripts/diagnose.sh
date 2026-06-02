#!/usr/bin/env bash
# Diagnose OOMKilled pods with wrong (injected) memory limits.
# Usage: ./diagnose.sh <namespace>
set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"

echo "=== Checking for OOMKilled pods in namespace: $NAMESPACE ==="

OOMKILLED=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | \
  awk '$3 == "OOMKilled" || ($4 > 0 && $3 ~ /CrashLoop/) {print $1}')

# Also check last state of running pods
ALL_OOMKILLED=$(kubectl get pods -n "$NAMESPACE" -o json 2>/dev/null | \
  jq -r '.items[] | select(
    (.status.containerStatuses[]?.lastState.terminated.reason == "OOMKilled") or
    (.status.containerStatuses[]?.state.terminated.reason == "OOMKilled")
  ) | .metadata.name' | sort -u)

COMBINED=$(printf "%s\n%s" "$OOMKILLED" "$ALL_OOMKILLED" | grep -v '^$' | sort -u)

if [ -z "$COMBINED" ]; then
  echo "No OOMKilled pods found."
else
  echo "OOMKilled pods:"
  echo "$COMBINED"
  echo ""

  for POD in $COMBINED; do
    echo "--- Pod: $POD ---"
    echo "Memory limits:"
    kubectl get pod "$POD" -n "$NAMESPACE" \
      -o jsonpath='{.spec.containers[0].resources}' 2>/dev/null | \
      python3 -m json.tool 2>/dev/null || echo "(parse failed)"
    echo ""
    echo "Last state:"
    kubectl get pod "$POD" -n "$NAMESPACE" \
      -o jsonpath='{.status.containerStatuses[0].lastState}' 2>/dev/null | \
      python3 -m json.tool 2>/dev/null || echo "(none)"
    echo ""
  done

  echo ""
  echo "Missing endpoints (services with no ready backends):"
  kubectl get endpoints -n "$NAMESPACE" 2>/dev/null | grep -E "^NAME|<none>"
fi

echo "PLAYBOOK: wrong-memory-limit"
