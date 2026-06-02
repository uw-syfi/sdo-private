#!/bin/bash
# Mitigate namespace memory quota fault in hotel-reservation

set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"

echo "=== Removing memory ResourceQuotas from namespace '$NAMESPACE' ==="
QUOTAS=$(kubectl get resourcequota -n "$NAMESPACE" -o json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for q in data['items']:
    if 'memory' in q.get('spec', {}).get('hard', {}):
        print(q['metadata']['name'])
" 2>/dev/null)

if [ -z "$QUOTAS" ]; then
    echo "No memory-based ResourceQuotas found — nothing to remove."
else
    for quota in $QUOTAS; do
        echo "Deleting ResourceQuota: $quota"
        kubectl delete resourcequota "$quota" -n "$NAMESPACE"
    done
    echo "Done. Memory quota(s) removed."
fi

echo ""
echo "=== Checking search pod status ==="
kubectl get pods -n "$NAMESPACE" -l io.kompose.service=search
