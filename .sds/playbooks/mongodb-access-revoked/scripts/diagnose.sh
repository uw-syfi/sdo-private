#!/usr/bin/env bash
# Detect MongoDB readWrite role revocation for Hotel Reservation services.
# Usage: ./diagnose.sh [geo|rate|all]   (default: all)
set -euo pipefail

NS="hotel-reservation"
SERVICES=("geo" "rate")
TARGET="${1:-all}"

check_service() {
  local svc="$1"
  local db="${svc}-db"
  echo "--- Checking $svc ($db) ---"

  # Check for the fault-injection ConfigMap
  if kubectl get configmap "failure-admin-${svc}" -n "$NS" &>/dev/null; then
    echo "  [WARN] failure-admin-${svc} ConfigMap found — fault may be active"
  fi

  # Query MongoDB for admin user roles
  local roles
  roles=$(kubectl exec -n "$NS" "deploy/mongodb-${svc}" -- \
    mongo admin -u admin -p admin --authenticationDatabase admin \
    --quiet --eval "JSON.stringify(db.getUser('admin').roles)" 2>/dev/null || echo "[]")

  if echo "$roles" | grep -q "\"db\":\"${db}\""; then
    echo "  [OK] admin has readWrite on ${db}"
  else
    echo "  [FAULT] admin is MISSING readWrite on ${db} — run mitigate.sh ${svc}"
    echo "  Current roles: $roles"
  fi
}

for svc in "${SERVICES[@]}"; do
  [[ "$TARGET" == "all" || "$TARGET" == "$svc" ]] && check_service "$svc"
done
