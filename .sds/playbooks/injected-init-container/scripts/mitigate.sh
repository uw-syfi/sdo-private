#!/usr/bin/env bash
# Mitigate: remove injected initContainers from a deployment
# Usage: mitigate.sh <deployment-name> [namespace]
set -euo pipefail

DEPLOY="${1:?Usage: mitigate.sh <deployment-name> [namespace]}"
NAMESPACE="${2:-hotel-reservation}"

echo "=== Removing initContainers from deployment/$DEPLOY in $NAMESPACE ==="

# Verify init containers exist
init_containers=$(kubectl get deployment "$DEPLOY" -n "$NAMESPACE" \
  -o jsonpath='{.spec.template.spec.initContainers}' 2>/dev/null)

if [[ -z "$init_containers" || "$init_containers" == "null" || "$init_containers" == "[]" ]]; then
  echo "No initContainers found on deployment/$DEPLOY — nothing to remove."
  exit 0
fi

echo "Found initContainers: $init_containers"
echo ""

kubectl patch deployment "$DEPLOY" -n "$NAMESPACE" --type=json \
  -p='[{"op": "remove", "path": "/spec/template/spec/initContainers"}]'

echo "Patched. Waiting for rollout..."
kubectl rollout status deployment/"$DEPLOY" -n "$NAMESPACE" --timeout=120s

echo ""
echo "=== Current pod status ==="
kubectl get pods -n "$NAMESPACE" -l "app=$DEPLOY"
