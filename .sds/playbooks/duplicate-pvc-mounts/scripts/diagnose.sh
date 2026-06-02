#!/usr/bin/env bash
# Diagnose duplicate-pvc-mounts scheduling deadlock.
# Usage: diagnose.sh <namespace>
set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"

find_pending_pods() {
    kubectl get pods -n "$NAMESPACE" --field-selector=status.phase=Pending \
        -o jsonpath='{.items[*].metadata.name}' 2>/dev/null
}

check_pod_events() {
    local pod="$1"
    kubectl describe pod -n "$NAMESPACE" "$pod" 2>/dev/null | grep -E "anti-affinity|volume node affinity|persistent volumes to bind" || true
}

check_deployment_affinity() {
    local deploy="$1"
    kubectl get deployment -n "$NAMESPACE" "$deploy" \
        -o jsonpath='{.spec.template.spec.affinity}' 2>/dev/null
}

check_deployment_volumes() {
    local deploy="$1"
    kubectl get deployment -n "$NAMESPACE" "$deploy" \
        -o jsonpath='{.spec.template.spec.volumes}' 2>/dev/null
}

echo "=== Pending pods in $NAMESPACE ==="
pending=$(find_pending_pods)
if [[ -z "$pending" ]]; then
    echo "No pending pods found."
    exit 0
fi

for pod in $pending; do
    echo ""
    echo "--- Pod: $pod ---"
    check_pod_events "$pod"

    # Derive deployment name from pod name (strip ReplicaSet hash suffix)
    deploy=$(kubectl get pod -n "$NAMESPACE" "$pod" \
        -o jsonpath='{.metadata.ownerReferences[0].name}' 2>/dev/null | sed 's/-[a-z0-9]\{10\}$//')

    if [[ -n "$deploy" ]]; then
        echo "Affinity on $deploy: $(check_deployment_affinity "$deploy")"
        echo "Volumes on $deploy:  $(check_deployment_volumes "$deploy")"
    fi
done

echo ""
echo "=== If events show both 'anti-affinity' and 'volume node affinity conflict', see: duplicate-pvc-mounts playbook ==="
