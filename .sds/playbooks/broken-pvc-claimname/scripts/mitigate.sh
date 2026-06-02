#!/usr/bin/env bash
# Mitigate broken PVC claimName by re-applying the source manifest.
# Usage: mitigate.sh <namespace> <svc>
# Example: mitigate.sh hotel-reservation rate

set -euo pipefail

NAMESPACE="${1:?Usage: mitigate.sh <namespace> <svc>}"
SVC="${2:?Usage: mitigate.sh <namespace> <svc>}"
MANIFEST="kubernetes/${SVC}/mongodb-${SVC}-deployment.yaml"

if [[ ! -f "${MANIFEST}" ]]; then
  echo "ERROR: Source manifest not found: ${MANIFEST}" >&2
  exit 1
fi

echo "Applying clean source manifest for mongodb-${SVC}..."
kubectl apply -f "${MANIFEST}" -n "${NAMESPACE}"

echo "Waiting for mongodb-${SVC} rollout..."
kubectl rollout status deployment/mongodb-"${SVC}" -n "${NAMESPACE}" --timeout=120s

echo "Done. Current pod status:"
kubectl get pods -n "${NAMESPACE}" -l "io.kompose.service=mongodb-${SVC}"
