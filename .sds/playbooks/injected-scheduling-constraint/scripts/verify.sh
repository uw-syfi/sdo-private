#!/usr/bin/env bash
# Verify all pods are Running and frontend is responsive.
# Usage: verify.sh [namespace]
set -euo pipefail

NS="${1:-hotel-reservation}"

echo "=== Pod status ==="
kubectl get pods -n "$NS"

pending=$(kubectl get pods -n "$NS" --field-selector=status.phase=Pending -o name 2>/dev/null | wc -l)
if [[ "$pending" -gt 0 ]]; then
  echo "FAIL: $pending pod(s) still Pending"
  exit 1
fi

not_ready=$(kubectl get pods -n "$NS" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' \
  | grep -v "True" | grep -v "^$" || true)
if [[ -n "$not_ready" ]]; then
  echo "FAIL: Pods not Ready:"
  echo "$not_ready"
  exit 1
fi

echo ""
echo "=== Frontend health check ==="
if kubectl exec -n "$NS" deploy/frontend -- wget -qO- http://localhost:5000/ 2>/dev/null | grep -q "html"; then
  echo "OK: frontend responds"
else
  echo "FAIL: frontend not responding"
  exit 1
fi

echo ""
echo "All checks passed."
