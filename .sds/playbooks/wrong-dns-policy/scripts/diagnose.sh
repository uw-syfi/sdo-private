#!/bin/bash
# Diagnose wrong DNS policy on deployments in a namespace.
# Usage: diagnose.sh <namespace>
# Checks all deployments for dnsPolicy: None with external nameservers.

set -euo pipefail

NAMESPACE="${1:-hotel-reservation}"

echo "=== Checking DNS policy on all deployments in namespace: $NAMESPACE ==="

AFFECTED=0

while IFS= read -r deploy; do
    DNS_POLICY=$(kubectl get deployment "$deploy" -n "$NAMESPACE" \
        -o jsonpath='{.spec.template.spec.dnsPolicy}' 2>/dev/null)
    DNS_CONFIG=$(kubectl get deployment "$deploy" -n "$NAMESPACE" \
        -o jsonpath='{.spec.template.spec.dnsConfig}' 2>/dev/null)

    if [[ "$DNS_POLICY" == "None" ]]; then
        echo "AFFECTED deployment=$deploy dnsPolicy=None dnsConfig=$DNS_CONFIG"
        AFFECTED=$((AFFECTED + 1))
    fi
done < <(kubectl get deployments -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n')

if [[ $AFFECTED -eq 0 ]]; then
    echo "No deployments with dnsPolicy=None found."
else
    echo ""
    echo "=> Playbook: .sds/playbooks/wrong-dns-policy/README.md"
fi
