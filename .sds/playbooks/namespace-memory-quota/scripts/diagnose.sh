#!/bin/bash
# Diagnose namespace memory quota fault in hotel-reservation

set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"

echo "=== ResourceQuota Check ==="
if kubectl get resourcequota -n "$NAMESPACE" 2>/dev/null | grep -q memory; then
    kubectl describe resourcequota -n "$NAMESPACE"
    echo ""
    echo "DETECTED: Memory ResourceQuota present — new pods without memory requests will be blocked."
    echo "PLAYBOOK: namespace-memory-quota"
else
    echo "No memory ResourceQuota found in namespace '$NAMESPACE'."
fi

echo ""
echo "=== Pods Without Memory Requests (deployments that cannot restart) ==="
kubectl get deployments -n "$NAMESPACE" -o json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for dep in data['items']:
    name = dep['metadata']['name']
    for c in dep['spec']['template']['spec']['containers']:
        req = c.get('resources', {}).get('requests', {})
        if 'memory' not in req:
            print(f'  {name}: NO memory request (cannot create new pods under quota)')
" 2>/dev/null || true
