#!/usr/bin/env bash
# Mitigate: Restore MongoDB image to the version matching PVC data.
# Usage: ./mitigate.sh [namespace] [correct-image]
# Example: ./mitigate.sh hotel-reservation mongo:4.4.6
set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"
CORRECT_IMAGE="${2:-mongo:4.4.6}"

echo "Restoring MongoDB deployments to image: $CORRECT_IMAGE"
for deployment in $(kubectl get deployments -n "$NAMESPACE" --no-headers | grep mongo | awk '{print $1}'); do
  container=$(kubectl get deployment "$deployment" -n "$NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].name}')
  echo "  Patching $deployment (container: $container)..."
  kubectl set image "deployment/$deployment" "${container}=${CORRECT_IMAGE}" -n "$NAMESPACE"
done

echo ""
echo "Waiting for rollout..."
for deployment in $(kubectl get deployments -n "$NAMESPACE" --no-headers | grep mongo | awk '{print $1}'); do
  kubectl rollout status "deployment/$deployment" -n "$NAMESPACE" --timeout=60s
done

echo ""
echo "Done. Run scripts/verify.sh to confirm health."
