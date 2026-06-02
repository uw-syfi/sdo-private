#!/usr/bin/env bash
# Mitigate broken PVC claimName for MongoDB deployments.
# For compound faults (extra volumes, nodeSelector), re-applies the source manifest.
# Usage: mitigate.sh <namespace> [svc]
#   If svc is omitted, fixes all 6 hotel-reservation MongoDB deployments.
# Example: mitigate.sh hotel-reservation rate

set -euo pipefail

NAMESPACE="${1:?Usage: mitigate.sh <namespace> [svc]}"
SVC="${2:-}"

fix_service() {
  local svc="$1"
  local manifest="kubernetes/${svc}/mongodb-${svc}-deployment.yaml"

  # Check for compound faults first (extra volumes, nodeSelector)
  local node_selector extra_vols
  node_selector=$(kubectl get deployment -n "${NAMESPACE}" "mongodb-${svc}" \
    -o jsonpath='{.spec.template.spec.nodeSelector}' 2>/dev/null || echo "")
  extra_vols=$(kubectl get deployment -n "${NAMESPACE}" "mongodb-${svc}" \
    -o jsonpath='{.spec.template.spec.volumes}' 2>/dev/null | \
    python3 -c "import sys,json; vols=json.load(sys.stdin); print(len(vols))" 2>/dev/null || echo "0")

  if [[ -n "${node_selector}" || "${extra_vols}" -gt 3 ]]; then
    echo "[${svc}] Compound fault detected (nodeSelector='${node_selector}', ${extra_vols} volumes). Re-applying source manifest..."
    if [[ -f "${manifest}" ]]; then
      kubectl apply -f "${manifest}" -n "${NAMESPACE}"
    else
      echo "  WARNING: Source manifest not found at ${manifest}. Patching claimName only."
      kubectl patch deployment -n "${NAMESPACE}" "mongodb-${svc}" --type=json \
        -p="[{\"op\": \"replace\", \"path\": \"/spec/template/spec/volumes/0/persistentVolumeClaim/claimName\", \"value\": \"${svc}-pvc\"}]"
    fi
  else
    echo "[${svc}] Simple claimName fault. Patching..."
    kubectl patch deployment -n "${NAMESPACE}" "mongodb-${svc}" --type=json \
      -p="[{\"op\": \"replace\", \"path\": \"/spec/template/spec/volumes/0/persistentVolumeClaim/claimName\", \"value\": \"${svc}-pvc\"}]"
  fi
}

if [[ -n "${SVC}" ]]; then
  fix_service "${SVC}"
else
  for svc in geo profile rate recommendation reservation user; do
    fix_service "${svc}"
  done
fi

echo ""
echo "Waiting for MongoDB pods to become Running..."
kubectl rollout status deployment/mongodb-geo deployment/mongodb-profile \
  deployment/mongodb-rate deployment/mongodb-recommendation \
  deployment/mongodb-reservation deployment/mongodb-user \
  -n "${NAMESPACE}" --timeout=120s 2>/dev/null || true

echo ""
echo "Current pod status:"
kubectl get pods -n "${NAMESPACE}" | grep -E "mongodb|NAME"

echo ""
echo "IMPORTANT: If MongoDB was down long enough for requests to be processed,"
echo "flush memcached-profile to clear any corrupted cached hotel entries:"
echo "  kubectl rollout restart deployment/memcached-profile -n ${NAMESPACE}"
