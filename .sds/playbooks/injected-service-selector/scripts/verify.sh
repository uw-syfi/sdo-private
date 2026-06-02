#!/usr/bin/env bash
# Verify that all services have healthy endpoints after selector fix.
# Usage: ./verify.sh [namespace]
set -euo pipefail

NS="${1:-hotel-reservation}"

echo "=== Endpoint health check in namespace: $NS ==="
all_ok=true
while IFS= read -r svc; do
  endpoints=$(kubectl get endpoints "$svc" -n "$NS" -o jsonpath='{.subsets}' 2>/dev/null)
  if [[ -z "$endpoints" ]]; then
    echo "FAIL: $svc has no endpoints"
    all_ok=false
  else
    ready=$(kubectl get endpoints "$svc" -n "$NS" -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null)
    echo "OK:   $svc -> $ready"
  fi
done < <(kubectl get svc -n "$NS" -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n')

echo ""
if $all_ok; then
  echo "All services have endpoints. Selector fix verified."
else
  echo "Some services still have no endpoints — check selectors."
  exit 1
fi
