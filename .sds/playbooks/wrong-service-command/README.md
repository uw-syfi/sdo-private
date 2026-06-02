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

## Stale Consul Cleanup

After patching, the old pod's consul registrations may persist briefly as "passing" because consul's TTL check hasn't expired. This causes two categories of stale entries:

1. **Stale wrong-service entry**: The old pod registered as the wrong service name (e.g. `srv-geo` from a pod that was supposed to be `profile`). If this IP is gone but the entry shows passing, it will route traffic to a dead backend.
2. **Stale correct-service entry**: Prior rollout pods may have registered under the correct name but their IP is now gone.

Check for and remove stale entries:
```bash
# List all instances for a given service including unhealthy
kubectl exec -n <namespace> deploy/<frontend-pod> -- sh -c \
  "curl -s 'http://consul:8500/v1/health/service/srv-<name>?passing=1'" | \
  python3 -c "import json,sys; [print(d['Service']['ID'], d['Service']['Address']) for d in json.load(sys.stdin)]"

# Cross-reference IPs with live pods
kubectl get pods -n <namespace> -o wide | grep <ip>

# Deregister stale entry if no pod has that IP
kubectl exec -n <namespace> deploy/<any-pod-with-curl> -- sh -c \
  "curl -s -X PUT http://consul:8500/v1/agent/service/deregister/<service-id>"
```

After cleanup, each service should have exactly 1 passing instance.

## Verification

After patching and stale cleanup, confirm the pod registers correctly and traffic flows:

```bash
# Pod should be Running/Ready
kubectl get pods -n <namespace> -l io.kompose.service=<name>

# Logs should show correct service starting
kubectl logs -n <namespace> deployment/<name> | grep -E "cmd/|registered in consul|srv-"

# Exactly 1 passing instance per service in consul
for svc in srv-search srv-geo srv-profile srv-rate srv-recommendation srv-reservation srv-user; do
  count=$(kubectl exec -n <namespace> deploy/frontend -- sh -c \
    "curl -s 'http://consul:8500/v1/health/service/${svc}?passing=1'" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
  echo "$svc: $count instances"
done

# Test the affected user-facing flow end-to-end
# e.g. GET /hotels?... should return hotel data with names, not 500
```
