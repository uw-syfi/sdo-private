#!/usr/bin/env bash
# Detect services with empty endpoints due to selector mismatch.
# Usage: ./diagnose.sh [namespace]
set -euo pipefail

NS="${1:-hotel-reservation}"

echo "=== Services with empty endpoints in namespace: $NS ==="
empty_svcs=()
while IFS= read -r svc; do
  endpoints=$(kubectl get endpoints "$svc" -n "$NS" -o jsonpath='{.subsets}' 2>/dev/null)
  if [[ -z "$endpoints" ]]; then
    empty_svcs+=("$svc")
  fi
done < <(kubectl get svc -n "$NS" -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n')

if [[ ${#empty_svcs[@]} -eq 0 ]]; then
  echo "All services have at least one endpoint. No selector mismatch detected."
  exit 0
fi

echo "Services with no endpoints:"
for svc in "${empty_svcs[@]}"; do
  echo "  - $svc"
done

echo ""
echo "=== Selector vs pod label comparison ==="
for svc in "${empty_svcs[@]}"; do
  echo "--- $svc ---"
  selector=$(kubectl get svc "$svc" -n "$NS" -o jsonpath='{.spec.selector}' 2>/dev/null)
  echo "  Live selector: $selector"

  # Get pods matching ANY of the standard labels (best-effort)
  base_label=$(kubectl get svc "$svc" -n "$NS" -o jsonpath='{.spec.selector.io\.kompose\.service}' 2>/dev/null || true)
  if [[ -n "$base_label" ]]; then
    echo "  Pods with io.kompose.service=$base_label:"
    kubectl get pods -n "$NS" -l "io.kompose.service=$base_label" --show-labels 2>/dev/null || echo "    (none)"
  fi
  echo ""
done

echo "Likely cause: extra label(s) in selector not present on pods."
echo "Playbook: .sds/playbooks/injected-service-selector/README.md"
