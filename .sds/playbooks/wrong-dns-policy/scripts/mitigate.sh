#!/bin/bash
# Remove injected dnsPolicy: None and dnsConfig from a deployment.
# Usage: mitigate.sh <namespace> <deployment-name>

set -euo pipefail

NAMESPACE="${1:?Usage: mitigate.sh <namespace> <deployment-name>}"
DEPLOY="${2:?Usage: mitigate.sh <namespace> <deployment-name>}"

echo "Patching deployment/$DEPLOY in namespace $NAMESPACE to remove dnsPolicy and dnsConfig..."

kubectl patch deployment "$DEPLOY" -n "$NAMESPACE" --type=json -p='[
  {"op": "remove", "path": "/spec/template/spec/dnsPolicy"},
  {"op": "remove", "path": "/spec/template/spec/dnsConfig"}
]'

echo "Waiting for rollout..."
kubectl rollout status deployment/"$DEPLOY" -n "$NAMESPACE" --timeout=60s

echo "Done. Pod DNS policy restored to ClusterFirst (default)."
