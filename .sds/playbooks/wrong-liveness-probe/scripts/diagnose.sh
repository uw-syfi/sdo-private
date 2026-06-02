#!/usr/bin/env bash
# Diagnose wrong/injected liveness probe faults in a namespace.
# Usage: ./diagnose.sh <namespace>
set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"

echo "=== Checking for liveness probe failures in namespace: $NAMESPACE ==="

# Find pods being killed by liveness probes
AFFECTED=$(kubectl get events -n "$NAMESPACE" \
  --field-selector reason=Killing \
  -o json 2>/dev/null | \
  jq -r '.items[] | select(.message | contains("liveness probe")) | .involvedObject.name' | sort -u)

if [ -z "$AFFECTED" ]; then
  echo "No liveness probe kill events found."
else
  echo "Pods being killed by failing liveness probes:"
  echo "$AFFECTED"
  echo ""

  for POD in $AFFECTED; do
    DEPLOY=$(kubectl get pod "$POD" -n "$NAMESPACE" \
      -o jsonpath='{.metadata.ownerReferences[0].name}' 2>/dev/null || true)
    RS=$(kubectl get replicaset "$DEPLOY" -n "$NAMESPACE" \
      -o jsonpath='{.metadata.ownerReferences[0].name}' 2>/dev/null || true)

    echo "--- Pod: $POD (Deployment: ${RS:-unknown}) ---"

    # Show liveness probe config
    echo "Liveness probe:"
    kubectl get pod "$POD" -n "$NAMESPACE" \
      -o jsonpath='{.spec.containers[0].livenessProbe}' 2>/dev/null | \
      python3 -m json.tool 2>/dev/null || \
      kubectl get pod "$POD" -n "$NAMESPACE" \
        -o jsonpath='{.spec.containers[0].livenessProbe}' 2>/dev/null

    # Show actual container ports
    echo ""
    echo "Container ports:"
    kubectl get pod "$POD" -n "$NAMESPACE" \
      -o jsonpath='{.spec.containers[0].ports}' 2>/dev/null

    # Show recent probe failure events
    echo ""
    echo "Recent probe failure events:"
    kubectl describe pod "$POD" -n "$NAMESPACE" 2>/dev/null | \
      grep -A 2 "Unhealthy\|Liveness" | tail -10
    echo ""
  done
fi

echo "PLAYBOOK: wrong-liveness-probe"
