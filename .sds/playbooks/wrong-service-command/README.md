# Wrong Service Command

## Symptom

A pod starts successfully and stays Running/Ready, but the service it exposes is wrong. Traffic to the affected service returns errors (5xx or empty responses) because consul never registers the expected service name, and calls to the service fail silently.

## Key Signals

- Frontend or upstream service logs show repeated `GetProfiles failed`, `GetRates failed`, or similar — RPC calls fail despite the target pod being Running/Ready.
- `kubectl describe deployment <name>` shows a `Command:` entry that does not match the deployment name (e.g. `geo` in the `profile` deployment).
- Pod logs show the wrong service starting: wrong `main.go` path (e.g. `cmd/geo/main.go` in the profile pod), wrong consul registration name (e.g. `srv-geo`), or connecting to the wrong MongoDB instance (e.g. `mongodb-geo:27017` from the profile pod).
- `kubectl exec -n <ns> <consul-pod> -- curl -s http://localhost:8500/v1/health/service/srv-<name>?passing=1` returns an empty list.

## Common Cause

The container `command` in a deployment spec was changed to a different service binary. In a single-image setup (where all services share one Docker image with multiple binaries), any binary name is a valid command, so the pod starts without error. The wrong service registers in consul under the wrong name, leaving the correct service unregistered.

## Diagnosis Steps

```bash
# 1. Find failing service from frontend/upstream logs
kubectl logs -n <namespace> deployment/frontend | grep -E "ERR|failed" | head -20

# 2. For the affected service, compare command vs deployment name
kubectl describe deployment <name> -n <namespace> | grep -A2 "Command:"

# 3. Check pod logs for wrong service identity
kubectl logs -n <namespace> deployment/<name> | grep -E "cmd/|registered in consul|srv-"

# 4. Confirm service is missing from consul
kubectl exec -n <namespace> <consul-pod> -- curl -s \
  "http://localhost:8500/v1/health/service/srv-<name>?passing=1" | head -5
```

## Mitigation

Patch the deployment command back to the correct service binary:

```bash
kubectl patch deployment <name> -n <namespace> --type='json' \
  -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/command/0", "value": "<correct-binary>"}]'
```

The correct binary name matches the deployment name (e.g. `profile` for the `profile` deployment). Verify against the source manifest:
```bash
grep -A3 "command:" kubernetes/<service>/<service>-deployment.yaml
```

**If the source manifest has the wrong command**, fix the file first, then apply:
```bash
kubectl apply -f kubernetes/<service>/<service>-deployment.yaml
```

## Verification

After patching, confirm the pod restarts and registers correctly:

```bash
# Pod should restart with 0 new restarts after rollout
kubectl get pods -n <namespace> -l io.kompose.service=<name>

# Logs should show correct service starting
kubectl logs -n <namespace> deployment/<name> | grep -E "cmd/|registered in consul|srv-"

# Test the affected user-facing flow end-to-end
# e.g. GET /hotels?... should return hotel data, not 500
```
