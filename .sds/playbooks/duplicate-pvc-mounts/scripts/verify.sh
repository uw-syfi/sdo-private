#!/usr/bin/env bash
# Verify duplicate-pvc-mounts mitigation: no pending pods, frontend healthy,
# end-to-end hotel search works.
# Usage: verify.sh <namespace>
set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"
PASS=0
FAIL=0

check() {
    local label="$1"; shift
    if "$@" &>/dev/null; then
        echo "  PASS: $label"
        ((PASS++)) || true
    else
        echo "  FAIL: $label"
        ((FAIL++)) || true
    fi
}

echo "=== Verifying $NAMESPACE ==="

# No pending pods
check "No Pending pods" bash -c \
    "[[ -z \"\$(kubectl get pods -n $NAMESPACE --field-selector=status.phase=Pending -o name 2>/dev/null)\" ]]"

# Frontend deployment ready
check "Frontend deployment 1/1 Ready" bash -c \
    "kubectl get deployment frontend -n $NAMESPACE -o jsonpath='{.status.readyReplicas}' | grep -q 1"

# Frontend endpoint exists
check "Frontend service endpoint present" bash -c \
    "kubectl get endpoints frontend -n $NAMESPACE -o jsonpath='{.subsets}' | grep -q addresses"

# Frontend HTTP responds
check "Frontend HTTP 200" kubectl exec -n "$NAMESPACE" deploy/frontend -- \
    wget -qO- http://localhost:5000/

# Hotel search returns results
check "Hotel search returns features" bash -c \
    "kubectl exec -n $NAMESPACE deploy/frontend -- wget -qO- \
     'http://localhost:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7879&lon=-122.4075&locale=en' \
     | grep -q 'features'"

# No injected PVC remains
check "frontend-pvc deleted" bash -c \
    "! kubectl get pvc frontend-pvc -n $NAMESPACE &>/dev/null"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
