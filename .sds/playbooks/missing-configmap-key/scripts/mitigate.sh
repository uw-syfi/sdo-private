#!/usr/bin/env bash
# mitigate.sh — Add a missing key to a hotel-reservation service ConfigMap and restart the deployment.
# Usage: bash mitigate.sh <service> <key> <value> [namespace]
#
# Example:
#   bash mitigate.sh geo GeoMongoAddress mongodb-geo:27017
set -euo pipefail

SERVICE="${1:?Usage: $0 <service> <key> <value> [namespace]}"
KEY="${2:?Usage: $0 <service> <key> <value> [namespace]}"
VALUE="${3:?Usage: $0 <service> <key> <value> [namespace]}"
NS="${4:-hotel-reservation}"

CM="${SERVICE}-config"

echo "=== Mitigating missing ConfigMap key ==="
echo "  Service:   $SERVICE"
echo "  ConfigMap: $CM"
echo "  Key:       $KEY"
echo "  Value:     $VALUE"
echo ""

# Read existing config.json
echo "--- Current config.json ---"
CURRENT=$(kubectl get configmap "$CM" -n "$NS" -o jsonpath='{.data.config\.json}' 2>/dev/null)
echo "$CURRENT" | python3 -m json.tool 2>/dev/null || echo "$CURRENT"

# Add the missing key using Python (safe JSON merge)
UPDATED=$(echo "$CURRENT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
data['$KEY'] = '$VALUE'
print(json.dumps(data, indent=2))
")

echo ""
echo "--- Updated config.json (with $KEY=$VALUE) ---"
echo "$UPDATED" | python3 -m json.tool

# Escape the JSON for kubectl patch
ESCAPED=$(echo "$UPDATED" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))")

# Patch the ConfigMap
echo ""
echo "--- Patching ConfigMap ---"
kubectl patch configmap "$CM" -n "$NS" --type merge \
  -p "{\"data\":{\"config.json\":$ESCAPED}}"

# Restart the deployment
echo ""
echo "--- Restarting deployment/$SERVICE ---"
kubectl rollout restart deployment/"$SERVICE" -n "$NS"
kubectl rollout status deployment/"$SERVICE" -n "$NS" --timeout=120s

echo ""
echo "=== Mitigation complete. Run verify.sh to confirm. ==="
