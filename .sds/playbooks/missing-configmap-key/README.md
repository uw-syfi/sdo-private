# Missing ConfigMap Key

## Symptom

A service pod is in `CrashLoopBackOff` or `Error` state. The pod logs show that the service reads an **empty value** for a required configuration key (e.g., a database address), then immediately fails on the first connection attempt.

Example log pattern:
```
Read database URL:                     ← empty value read
Initializing DB connection...
no reachable servers
panic: no reachable servers
```

The ConfigMap mounts and the pod starts — `CreateContainerConfigError` is **not** seen. The issue is a missing or blank key inside the mounted config file.

## Difference from Related Issues

| Issue | Symptom | Signal |
|-------|---------|--------|
| **Missing ConfigMap key** (this playbook) | Pod starts, reads empty value, panics | Pod reaches `Running` briefly then crashes; log shows empty field |
| Missing ConfigMap object | Pod stuck in `CreateContainerConfigError` | Pod never starts; `kubectl describe pod` shows `configmap not found` |

## Diagnosis

```bash
# 1. Find the crashing service
kubectl get pods -n hotel-reservation | grep -v Running

# 2. Check the pod logs for an empty-value read
kubectl logs -n hotel-reservation <pod-name>
# Look for: "Read database URL: " (blank after colon)

# 3. Identify which ConfigMap the service uses
kubectl describe pod -n hotel-reservation <pod-name> | grep -A5 "Volumes\|configMap"

# 4. Inspect the ConfigMap contents for the missing key
kubectl get configmap -n hotel-reservation <configmap-name> -o yaml
# Compare with peer services (e.g., profile-config, rate-config) to find which key is missing

# 5. Find the correct expected value by checking source code
# In hotel-reservation: cmd/<service>/main.go reads the config key name
# e.g., "GeoMongoAddress" -> mongodb-<service>:27017
```

Run the diagnose script for an automated check:
```bash
bash .sds/playbooks/missing-configmap-key/scripts/diagnose.sh
```

## Root Cause

The ConfigMap for a service is missing a required key in its `config.json`. The injected/deployed ConfigMap omitted a key (e.g., `GeoMongoAddress`) that the service reads on startup. Go's `encoding/json` unmarshals a missing key as the zero value (empty string `""`), which the service passes directly as a connection address — resulting in an immediate panic.

## Mitigation

```bash
# 1. Get the current config.json content
kubectl get configmap <configmap-name> -n hotel-reservation -o jsonpath='{.data.config\.json}' | python3 -m json.tool

# 2. Patch the ConfigMap to add the missing key
# Example: adding GeoMongoAddress to geo-config
kubectl patch configmap geo-config -n hotel-reservation --type merge -p '{
  "data": {
    "config.json": "{\"consulAddress\":\"consul:8500\",\"jaegerAddress\":\"jaeger:6831\",\"FrontendPort\":\"5000\",\"GeoPort\":\"8083\",\"GeoMongoAddress\":\"mongodb-geo:27017\",...}"
  }
}'

# Better approach: use the mitigate script or edit inline
# The mitigate script reconstructs the correct config from source defaults

# 3. Restart the deployment to pick up the new ConfigMap
kubectl rollout restart deployment/<service> -n hotel-reservation

# 4. Wait for rollout
kubectl rollout status deployment/<service> -n hotel-reservation --timeout=120s
```

Run the mitigate script for hotel-reservation:
```bash
bash .sds/playbooks/missing-configmap-key/scripts/mitigate.sh <service> <key> <value>
```

## Verification

```bash
# Check all pods are Running
kubectl get pods -n hotel-reservation

# Confirm endpoint is populated
kubectl get endpoints -n hotel-reservation | grep <service>

# Check service logs show the value was read correctly
kubectl logs -n hotel-reservation deploy/<service> | head -5

# Run end-to-end test via frontend
kubectl run curl-verify -n hotel-reservation --restart=Never --image=curlimages/curl:latest \
  --command -- curl -fsS --max-time 15 http://frontend:5000/
kubectl wait --for=condition=ready pod/curl-verify -n hotel-reservation --timeout=60s
kubectl logs curl-verify -n hotel-reservation
kubectl delete pod curl-verify -n hotel-reservation --ignore-not-found
```

## Pattern: Hotel Reservation Config Key Naming

| Service | ConfigMap | Missing Key Pattern |
|---------|-----------|---------------------|
| geo | geo-config | `GeoMongoAddress` |
| profile | profile-config | `ProfileMongoAddress` |
| rate | rate-config | `RateMongoAddress` |
| recommendation | recommendation-config | `RecommendMongoAddress` |
| reservation | reservation-config | `ReserveMongoAddress` |
| user | user-config | `UserMongoAddress` |

The correct MongoDB address follows the pattern: `mongodb-<service>:27017`
