#!/usr/bin/env bash
# Diagnose: detect microservice pods crashing because a dependency Service is missing.
# Usage: ./diagnose.sh [namespace]
# Checks for pods in CrashLoopBackOff where logs mention a hostname that has no matching Service.

set -euo pipefail

NS="${1:-hotel-reservation}"

echo "=== Pods not Running/Ready ==="
kubectl get pods -n "$NS" --no-headers | awk '$3 != "Running" || $2 !~ /^[0-9]+\/[0-9]+$/ || $2 != $2 {print}' | \
  awk -F'/' 'NR==1 || ($2 != "" && $1 < $2) {print}' || \
  kubectl get pods -n "$NS" | grep -v "Running\|Completed\|NAME" || true

echo ""
echo "=== Services in namespace ==="
kubectl get svc -n "$NS" --no-headers | awk '{print $1}'

echo ""
echo "=== Endpoints with no addresses (missing backend) ==="
kubectl get endpoints -n "$NS" --no-headers | awk '$2 == "<none>" {print $1}'

echo ""
echo "=== Checking CrashLoopBackOff pods for missing-service pattern ==="
CRASHERS=$(kubectl get pods -n "$NS" --no-headers | awk '$3 == "CrashLoopBackOff" {print $1}')

if [[ -z "$CRASHERS" ]]; then
  echo "No CrashLoopBackOff pods found."
  exit 0
fi

for pod in $CRASHERS; do
  echo ""
  echo "--- Pod: $pod ---"
  # Try previous logs first (most recent crash), then current
  LOGS=$(kubectl logs -n "$NS" "$pod" --previous 2>/dev/null || kubectl logs -n "$NS" "$pod" 2>/dev/null || true)
  if echo "$LOGS" | grep -qiE "no reachable servers|dial tcp.*connection refused|no such host"; then
    echo "[MATCH] Connection failure in logs:"
    echo "$LOGS" | grep -iE "no reachable servers|dial tcp.*connection refused|no such host|Read database URL" | head -10

    # Extract hostname from log lines like "mongodb-rate:27017"
    HOSTNAMES=$(echo "$LOGS" | grep -oE '[a-z0-9-]+:[0-9]+' | cut -d: -f1 | sort -u)
    for host in $HOSTNAMES; do
      if ! kubectl get svc "$host" -n "$NS" &>/dev/null; then
        echo "[FAULT] Service '$host' does not exist in namespace '$NS'"
        echo "  => Check: kubectl get deploy $host -n $NS"
        echo "  => Fix:   kubectl apply -f .../knative/${host}-service.yaml -n $NS"
        echo "  => PLAYBOOK: missing-service"
      fi
    done
  fi
done
