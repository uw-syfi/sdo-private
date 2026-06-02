#!/usr/bin/env bash
# verify.sh — Verify that a service is healthy after fixing a missing ConfigMap key.
# Usage: bash verify.sh <service> [namespace]
set -euo pipefail

SERVICE="${1:?Usage: $0 <service> [namespace]}"
NS="${2:-hotel-reservation}"

echo "=== Verifying $SERVICE health ==="

# Check pod readiness
echo "--- Pod status ---"
kubectl get pods -n "$NS" -l "io.kompose.service=$SERVICE"

# Check logs for successful DB URL read
echo ""
echo "--- Service startup logs ---"
kubectl logs -n "$NS" "deploy/$SERVICE" --tail=10 2>/dev/null || echo "(no logs)"

# Check endpoint populated
echo ""
echo "--- Endpoint ---"
kubectl get endpoints -n "$NS" "$SERVICE" 2>/dev/null || echo "(no endpoint)"

# End-to-end test via frontend
echo ""
echo "--- End-to-end HTTP test via frontend ---"
kubectl delete pod curl-verify -n "$NS" --ignore-not-found 2>/dev/null || true
kubectl run curl-verify -n "$NS" --restart=Never --image=curlimages/curl:latest \
  --command -- curl -fsS --max-time 15 http://frontend:5000/
kubectl wait --for=condition=ready pod/curl-verify -n "$NS" --timeout=60s 2>/dev/null || true
kubectl logs curl-verify -n "$NS" 2>/dev/null | head -5 || echo "(no output)"
kubectl delete pod curl-verify -n "$NS" --ignore-not-found 2>/dev/null || true

echo ""
echo "=== Verification complete ==="
