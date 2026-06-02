#!/usr/bin/env bash
# Diagnose: check for pods stuck in Init state due to injected init containers
set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"

echo "=== Checking for pods stuck in Init state ==="
stuck=$(kubectl get pods -n "$NAMESPACE" --no-headers | grep "Init:" || true)
if [[ -z "$stuck" ]]; then
  echo "No pods in Init state."
  exit 0
fi

echo "Stuck pods:"
echo "$stuck"
echo ""

# For each stuck pod, identify the hanging init container
while IFS= read -r line; do
  pod=$(echo "$line" | awk '{print $1}')
  echo "=== Pod: $pod ==="
  kubectl get pod "$pod" -n "$NAMESPACE" \
    -o jsonpath='{range .spec.initContainers[*]}initContainer: {.name} | image: {.image} | cmd: {.command}{"\n"}{end}'
  echo ""
done <<< "$stuck"

# Show deployment-level info for affected deployments
deployments=$(kubectl get pods -n "$NAMESPACE" --no-headers | grep "Init:" | \
  awk '{print $1}' | sed 's/-[a-z0-9]*-[a-z0-9]*$//' | sort -u || true)
for deploy in $deployments; do
  echo "=== Deployment spec initContainers for $deploy ==="
  kubectl get deployment "$deploy" -n "$NAMESPACE" \
    -o jsonpath='{.spec.template.spec.initContainers}' 2>/dev/null || true
  echo ""
  echo "=== last-applied-configuration initContainers (should be empty if injected) ==="
  kubectl get deployment "$deploy" -n "$NAMESPACE" \
    -o jsonpath='{.metadata.annotations.kubectl\.kubernetes\.io/last-applied-configuration}' 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('spec',{}).get('template',{}).get('spec',{}).get('initContainers','(none)'))" 2>/dev/null || echo "(could not parse)"
  echo ""
done
