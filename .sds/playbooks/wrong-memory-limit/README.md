# Wrong Memory Limit (OOMKilled)

## Symptom

A pod is in `OOMKilled` state and keeps restarting. The pod shows `0/1` readiness and the container restart count increases. The service that depends on this pod loses its endpoint (shown as `<none>` in `kubectl get endpoints`), causing dependent services to fail.

## Key Signals

- `kubectl get pods -n <namespace>` shows a pod with status `OOMKilled` and restart count > 0
- `kubectl describe pod <pod>` shows `OOM killed` in Last State or container exit code 137
- `kubectl get endpoints -n <namespace>` shows `<none>` for the service backed by the OOMKilled pod
- The memory limit in the deployment is unrealistically small for the workload (e.g. `10Mi` for MongoDB which needs hundreds of MB)

## Common Causes

- A memory limit was injected into the deployment that is far below the process's actual working set
- MongoDB, Redis, and other stateful services typically need at least 128Mi–512Mi to function

## Diagnosis Steps

```bash
# 1. Find OOMKilled pod
kubectl get pods -n <namespace> | grep -E "OOM|0/1"

# 2. Check memory limits
kubectl get deployment <name> -n <namespace> \
  -o jsonpath='{.spec.template.spec.containers[0].resources}'

# 3. Confirm against source manifest
grep -A 10 "resources:" kubernetes/<service>/<deployment>.yaml

# 4. Check missing endpoint
kubectl get endpoints -n <namespace> | grep "<none>"
```

## Mitigation

Remove or raise the injected memory limit to match the source manifest.

**If source manifest has no memory limit** (restore to source state):
```bash
kubectl patch deployment <name> -n <namespace> --type='json' \
  -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/resources", "value": {"requests": {"cpu": "100m"}, "limits": {"cpu": "1"}}}]'
```

**If source manifest has a reasonable memory limit** (e.g. 256Mi):
```bash
kubectl patch deployment <name> -n <namespace> --type='json' \
  -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/resources/limits/memory", "value": "256Mi"}]'
```

## Verification

```bash
# Pod should be Running
kubectl get pods -n <namespace> -l <selector>

# Endpoint should be populated
kubectl get endpoints -n <namespace> <service-name>

# Dependent service should recover (logs should show successful DB connection)
kubectl logs -n <namespace> deploy/<dependent-service> --tail=20
```
