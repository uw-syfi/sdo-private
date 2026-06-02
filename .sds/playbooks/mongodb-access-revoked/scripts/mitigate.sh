#!/usr/bin/env bash
# Restore MongoDB readWrite role for a Hotel Reservation service's admin user.
# Usage: ./mitigate.sh <service>   (e.g. geo, rate)
set -euo pipefail

NS="hotel-reservation"
SVC="${1:?Usage: mitigate.sh <service> (e.g. geo, rate)}"
DB="${SVC}-db"

echo "Granting readWrite on ${DB} to admin..."
kubectl exec -n "$NS" "deploy/mongodb-${SVC}" -- \
  mongo admin -u admin -p admin --authenticationDatabase admin \
  --eval "db.grantRolesToUser('admin', [{role: 'readWrite', db: '${DB}'}]);"

echo "Verifying..."
kubectl exec -n "$NS" "deploy/mongodb-${SVC}" -- \
  mongo admin -u admin -p admin --authenticationDatabase admin \
  --quiet --eval "JSON.stringify(db.getUser('admin').roles)" 2>/dev/null

echo "Done. Wait for the ${SVC} pod to restart and become Ready."
