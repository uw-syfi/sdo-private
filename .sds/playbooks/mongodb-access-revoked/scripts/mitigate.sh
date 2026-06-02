#!/usr/bin/env bash
# Restore MongoDB access for a Hotel Reservation service's admin user.
# Handles both variants:
#   Variant A: readWrite role revoked  (admin user exists but lacks permission)
#   Variant B: admin user deleted      (authentication fails entirely)
# Usage: ./mitigate.sh <service>   (e.g. geo, rate)
set -euo pipefail

NS="hotel-reservation"
SVC="${1:?Usage: mitigate.sh <service> (e.g. geo, rate)}"
DB="${SVC}-db"

mongo_root() {
  kubectl exec -n "$NS" "deploy/mongodb-${SVC}" -- \
    mongo admin -u root -p root --authenticationDatabase admin "$@"
}

# Detect variant: try connecting as admin
echo "Detecting fault variant..."
if kubectl exec -n "$NS" "deploy/mongodb-${SVC}" -- \
    mongo admin -u admin -p admin --authenticationDatabase admin \
    --quiet --eval "db.getUser('admin')" &>/dev/null; then
  echo "  admin user exists — Variant A (role revoked)"
  VARIANT="A"
else
  echo "  admin user unreachable — Variant B (user deleted)"
  VARIANT="B"
fi

if [ "$VARIANT" = "B" ]; then
  echo "Recreating admin user with readWrite on ${DB}..."
  mongo_root --eval "
    db.createUser({
      user: 'admin',
      pwd: 'admin',
      roles: [
        {role: 'userAdminAnyDatabase', db: 'admin'},
        {role: 'readWrite', db: '${DB}'}
      ]
    });
  "
else
  echo "Granting readWrite on ${DB} to admin (via root credentials)..."
  mongo_root --eval "db.grantRolesToUser('admin', [{role: 'readWrite', db: '${DB}'}]);"
fi

echo "Verifying admin user roles..."
mongo_root --quiet --eval "JSON.stringify(db.getUser('admin').roles)"

echo "Restarting ${SVC} deployment..."
kubectl rollout restart deployment/"${SVC}" -n "$NS"
kubectl rollout status deployment/"${SVC}" -n "$NS" --timeout=60s

echo "Mitigation complete for ${SVC}."
