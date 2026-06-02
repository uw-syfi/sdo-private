#!/usr/bin/env bash
# Verify the Hotel Reservation application is healthy after MongoDB access restore.
set -euo pipefail

NS="hotel-reservation"

echo "=== Pod readiness ==="
kubectl get pods -n "$NS" --no-headers

echo ""
echo "=== Consul service registrations ==="
kubectl exec -n "$NS" deploy/consul -- curl -s http://localhost:8500/v1/catalog/services 2>/dev/null

echo ""
echo "=== End-to-end hotel search ==="
kubectl run verify-curl -n "$NS" --restart=Never --image=curlimages/curl:latest \
  --command -- curl -fsS \
  "http://frontend:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7749&lon=-122.4194&customerName=test_user_0" \
  2>/dev/null
sleep 5
kubectl logs -n "$NS" verify-curl 2>/dev/null
kubectl delete pod verify-curl -n "$NS" --ignore-not-found 2>/dev/null
echo ""
echo "If the output above is a JSON FeatureCollection with hotels, the application is healthy."
