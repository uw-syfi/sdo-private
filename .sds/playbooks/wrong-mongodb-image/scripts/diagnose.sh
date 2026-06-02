#!/usr/bin/env bash
# Diagnose: MongoDB image/FCV version mismatch
# Checks all MongoDB pods for the "Wrong mongod version" fatal error.
set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"

echo "=== MongoDB pod status ==="
kubectl get pods -n "$NAMESPACE" | grep mongo || echo "(no mongodb pods found)"

echo ""
echo "=== Checking for FCV mismatch in pod logs ==="
found=0
while IFS= read -r pod; do
  logs=$(kubectl logs -n "$NAMESPACE" "$pod" --previous 2>/dev/null || kubectl logs -n "$NAMESPACE" "$pod" 2>/dev/null || true)
  if echo "$logs" | grep -q "featureCompatibilityVersion\|Wrong mongod version\|UPGRADE PROBLEM"; then
    echo "FAULT DETECTED in $pod:"
    echo "$logs" | grep -E "featureCompatibilityVersion|Wrong mongod version|UPGRADE PROBLEM" | head -3
    found=1
  fi
done < <(kubectl get pods -n "$NAMESPACE" --no-headers | grep mongo | awk '{print $1}')

if [[ $found -eq 1 ]]; then
  echo ""
  echo "=== Current image vs source manifest ==="
  kubectl get deployments -n "$NAMESPACE" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.template.spec.containers[0].image}{"\n"}{end}' | grep mongo
  echo ""
  echo "PLAYBOOK: .sds/playbooks/wrong-mongodb-image"
else
  echo "No FCV mismatch detected."
fi
