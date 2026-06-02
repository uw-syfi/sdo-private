#!/usr/bin/env bash
# Restore a service selector to the standard single-label form.
# Usage: ./mitigate.sh <service-name> [namespace]
# Example: ./mitigate.sh frontend hotel-reservation
set -euo pipefail

SVC="${1:?Usage: $0 <service-name> [namespace]}"
NS="${2:-hotel-reservation}"

echo "Patching selector for service: $SVC (namespace: $NS)"
echo "Current selector: $(kubectl get svc "$SVC" -n "$NS" -o jsonpath='{.spec.selector}')"

kubectl patch svc "$SVC" -n "$NS" --type=json \
  -p="[{\"op\": \"replace\", \"path\": \"/spec/selector\", \"value\": {\"io.kompose.service\": \"$SVC\"}}]"

echo "Patched. New endpoints:"
kubectl get endpoints "$SVC" -n "$NS"
