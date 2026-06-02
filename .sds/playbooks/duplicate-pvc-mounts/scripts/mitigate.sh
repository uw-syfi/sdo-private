#!/usr/bin/env bash
# Mitigate duplicate-pvc-mounts fault: remove injected anti-affinity, PVC volume,
# volumeMount, scale to 1 replica, and delete the orphaned PVC.
# Usage: mitigate.sh <namespace> <deployment>
set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"
DEPLOY="${2:?Usage: mitigate.sh <namespace> <deployment>}"
PVC_NAME="${DEPLOY}-pvc"

echo "=== Patching deployment $DEPLOY in $NAMESPACE ==="

# Verify the deployment exists
kubectl get deployment -n "$NAMESPACE" "$DEPLOY" > /dev/null

# Check what volumes are present — only remove injected entry if containers
# already have volumeMounts (to avoid clobbering pre-existing mounts)
volume_count=$(kubectl get deployment -n "$NAMESPACE" "$DEPLOY" \
    -o jsonpath='{.spec.template.spec.volumes}' | python3 -c "import sys,json; v=json.load(sys.stdin); print(len(v))" 2>/dev/null || echo 0)

volumemount_count=$(kubectl get deployment -n "$NAMESPACE" "$DEPLOY" \
    -o jsonpath='{.spec.template.spec.containers[0].volumeMounts}' \
    | python3 -c "import sys,json; v=json.load(sys.stdin); print(len(v))" 2>/dev/null || echo 0)

patch_ops='[
  {"op": "remove", "path": "/spec/template/spec/affinity"},
  {"op": "replace", "path": "/spec/replicas", "value": 1}
]'

# Only remove volumes/volumeMounts arrays entirely if the injected PVC is
# the sole entry; otherwise do targeted removal (safer default: bail out and
# ask the operator to review manually).
if [[ "$volume_count" -eq 1 && "$volumemount_count" -eq 1 ]]; then
    patch_ops='[
      {"op": "remove", "path": "/spec/template/spec/affinity"},
      {"op": "remove", "path": "/spec/template/spec/volumes"},
      {"op": "remove", "path": "/spec/template/spec/containers/0/volumeMounts"},
      {"op": "replace", "path": "/spec/replicas", "value": 1}
    ]'
elif [[ "$volume_count" -gt 1 ]]; then
    echo "WARNING: multiple volumes detected ($volume_count). Removing only affinity and scaling to 1."
    echo "Review and manually remove the injected PVC volume entry."
fi

kubectl patch deployment -n "$NAMESPACE" "$DEPLOY" --type=json -p="$patch_ops"
echo "Deployment patched."

# Delete the orphaned PVC if it exists
if kubectl get pvc -n "$NAMESPACE" "$PVC_NAME" &>/dev/null; then
    kubectl delete pvc -n "$NAMESPACE" "$PVC_NAME"
    echo "Deleted PVC $PVC_NAME."
else
    echo "PVC $PVC_NAME not found (already deleted or not present)."
fi

echo ""
echo "=== Waiting for rollout ==="
kubectl rollout status deployment -n "$NAMESPACE" "$DEPLOY" --timeout=120s
