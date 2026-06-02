#!/usr/bin/env bash
# Detect MongoDB readWrite role revocation for Hotel Reservation services.
# Usage: ./diagnose.sh [geo|rate|all]   (default: all)
set -euo pipefail

NS="hotel-reservation"
SERVICES=("geo" "rate")
TARGET="${1:-all}"

# Get the earliest pod creation timestamp in the namespace as the cluster baseline.
cluster_start() {
  kubectl get pods -n "$NS" \
    -o jsonpath='{range .items[*]}{.metadata.creationTimestamp}{"\n"}{end}' 2>/dev/null |
    sort | head -1
}

check_service() {
  local svc="$1"
  local db="${svc}-db"
  echo "--- Checking $svc ($db) ---"

  # 1. Check for the fault-injection ConfigMap
  if kubectl get configmap "failure-admin-${svc}" -n "$NS" &>/dev/null; then
    echo "  [WARN] failure-admin-${svc} ConfigMap found — fault may be active"
  fi

  # 2. Query MongoDB for admin user roles
  local roles
  roles=$(kubectl exec -n "$NS" "deploy/mongodb-${svc}" -- \
    mongo admin -u admin -p admin --authenticationDatabase admin \
    --quiet --eval "JSON.stringify(db.getUser('admin').roles)" 2>/dev/null || echo "AUTH_FAILED")

  if [ "$roles" = "AUTH_FAILED" ]; then
    echo "  [FAULT] admin:admin authentication FAILED — user may be deleted"
    echo "  Try: kubectl exec -n $NS deploy/mongodb-${svc} -- mongo admin -u root -p root --authenticationDatabase admin --eval 'db.getUsers()'"
  elif echo "$roles" | grep -q "\"db\":\"${db}\""; then
    echo "  [OK] admin has readWrite on ${db}"
  else
    echo "  [FAULT] admin is MISSING readWrite on ${db} — run mitigate.sh ${svc}"
    echo "  Current roles: $roles"
  fi

  # 3. Check service pod creation timestamp vs cluster start (stealth variant detection)
  local svc_pod_ts cluster_ts
  svc_pod_ts=$(kubectl get pods -n "$NS" -l "io.kompose.service=${svc}" \
    -o jsonpath='{.items[0].metadata.creationTimestamp}' 2>/dev/null || echo "unknown")
  cluster_ts=$(cluster_start)
  if [ "$svc_pod_ts" != "unknown" ] && [ "$svc_pod_ts" \> "$cluster_ts" ]; then
    echo "  [INFO] ${svc} pod created at $svc_pod_ts (after cluster start $cluster_ts) — pod was re-created post-injection"
  else
    echo "  [INFO] ${svc} pod creation timestamp matches cluster start — pod not deleted since deployment"
  fi

  # 4. Scan MongoDB audit log for revokeRolesFromUser to confirm script was run
  local revoke_count
  revoke_count=$(kubectl logs -n "$NS" "deploy/mongodb-${svc}" 2>/dev/null |
    grep -c "revokeRolesFromUser" || true)
  if [ "$revoke_count" -gt 0 ]; then
    echo "  [WARN] MongoDB audit log shows $revoke_count revokeRolesFromUser operation(s) — fault WAS applied"
  else
    echo "  [INFO] No revokeRolesFromUser in MongoDB logs — fault may NOT have been applied"
  fi
}

for svc in "${SERVICES[@]}"; do
  [[ "$TARGET" == "all" || "$TARGET" == "$svc" ]] && check_service "$svc"
done
