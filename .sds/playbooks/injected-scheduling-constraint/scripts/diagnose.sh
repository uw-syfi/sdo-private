#!/usr/bin/env bash
# Diagnose injected scheduling constraints causing Pending pods.
# Usage: diagnose.sh [namespace]
set -euo pipefail

NS="${1:-hotel-reservation}"

echo "=== Pending pods in $NS ==="
kubectl get pods -n "$NS" --field-selector=status.phase=Pending 2>/dev/null || true

pending_pods=$(kubectl get pods -n "$NS" --field-selector=status.phase=Pending -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)
if [[ -z "$pending_pods" ]]; then
  echo "No Pending pods found."
  exit 0
fi

for pod in $pending_pods; do
  echo ""
  echo "--- Pod: $pod ---"
  kubectl describe pod -n "$NS" "$pod" 2>/dev/null | grep -A3 "FailedScheduling" || true

  owner=$(kubectl get pod -n "$NS" "$pod" \
    -o jsonpath='{.metadata.ownerReferences[0].name}' 2>/dev/null || true)
  owner_kind=$(kubectl get pod -n "$NS" "$pod" \
    -o jsonpath='{.metadata.ownerReferences[0].kind}' 2>/dev/null || true)

  if [[ "$owner_kind" == "ReplicaSet" ]]; then
    deploy=$(kubectl get replicaset -n "$NS" "$owner" \
      -o jsonpath='{.metadata.ownerReferences[0].name}' 2>/dev/null || true)
    if [[ -n "$deploy" ]]; then
      echo "  Deployment: $deploy"
      echo "  Resources:"
      kubectl get deployment -n "$NS" "$deploy" \
        -o jsonpath='{.spec.template.spec.containers[0].resources}' 2>/dev/null || true
      echo ""
      echo "  Ports:"
      kubectl get deployment -n "$NS" "$deploy" \
        -o jsonpath='{.spec.template.spec.containers[0].ports}' 2>/dev/null || true
      echo ""
    fi
  fi
done
