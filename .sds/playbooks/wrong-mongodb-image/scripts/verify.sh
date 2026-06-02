#!/usr/bin/env bash
# Verify: All MongoDB pods running and frontend end-to-end healthy.
set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"

echo "=== MongoDB pod readiness ==="
kubectl get pods -n "$NAMESPACE" | grep mongo

echo ""
echo "=== Checking all MongoDB pods are Running ==="
not_running=$(kubectl get pods -n "$NAMESPACE" --no-headers | grep mongo | grep -v Running | wc -l)
if [[ $not_running -gt 0 ]]; then
  echo "FAIL: $not_running MongoDB pod(s) not Running"
  kubectl get pods -n "$NAMESPACE" | grep mongo | grep -v Running
  exit 1
fi
echo "OK: all MongoDB pods Running"

echo ""
echo "=== End-to-end search request ==="
kubectl delete pod verify-curl -n "$NAMESPACE" --ignore-not-found >/dev/null 2>&1 || true
kubectl run verify-curl -n "$NAMESPACE" --restart=Never --image=curlimages/curl:latest --command -- \
  curl -fsS -o /dev/null -w "%{http_code}" \
  "http://frontend:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7749&lon=-122.4194&userID=user1"
sleep 5
status=$(kubectl logs verify-curl -n "$NAMESPACE" 2>/dev/null)
kubectl delete pod verify-curl -n "$NAMESPACE" --ignore-not-found >/dev/null 2>&1 || true
if [[ "$status" == "200" ]]; then
  echo "OK: frontend returned HTTP 200"
else
  echo "FAIL: frontend returned HTTP $status"
  exit 1
fi
