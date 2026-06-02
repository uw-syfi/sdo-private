# Wrong Liveness Probe

## Symptom

A pod is repeatedly killed and restarted despite starting successfully. Pod events show `Liveness probe failed` followed by `Container ... failed liveness probe, will be restarted`.

Two distinct patterns:

**Pattern A — Wrong target**: Pod starts successfully and serves traffic but probe hits wrong port/protocol/path, so it always fails.

**Pattern B — Too-aggressive timing**: Pod has a slow initialization window (returns 503 during startup) but the probe's `initialDelaySeconds` is 0 and `failureThreshold` is 1, so the probe kills the container before it can ever initialize, causing CrashLoopBackOff.

## Key Signals

- `kubectl describe pod <pod>` shows repeated `Liveness probe failed` events
- **Pattern A**: Container starts fine (logs show service registration) but is killed shortly after; probe error shows connection refused, bad protocol, or 404
  - **Wrong port**: `dial tcp <ip>:PORT: connect: connection refused`
  - **Wrong protocol**: `malformed HTTP response "\x00\x00\x00..."` (HTTP/1.x probe hit gRPC)
  - **Wrong path**: `404 Not Found`
- **Pattern B**: Container crashes quickly (within 1–2 seconds of start); logs show `HTTP probe failed with statuscode: 503`; pod never reaches Running/Ready; the service code has an explicit init delay (e.g. `INIT_SECONDS = 15`, returns 503 until elapsed)

## Common Causes

1. **Wrong port**: Probe port does not match the container's actual listening port.
2. **Wrong protocol**: An `httpGet` probe applied to a gRPC service.
3. **Wrong path**: Probe path not implemented by the service.
4. **Too-aggressive timing**: `initialDelaySeconds=0` + `failureThreshold=1` on a service with a slow startup phase that returns 503 before it is ready.

## Diagnosis Steps

```bash
# 1. Find unhealthy pod and check events
kubectl describe pod -n <namespace> <pod-name>
# Look for: "Liveness probe failed" events with error detail

# 2. Compare probe config vs actual container port
kubectl get deployment <name> -n <namespace> -o jsonpath='{.spec.template.spec.containers[0].livenessProbe}'
kubectl get deployment <name> -n <namespace> -o jsonpath='{.spec.template.spec.containers[0].ports}'

# 3. Check source manifest for the correct probe (or absence of probe)
grep -r "livenessProbe" kubernetes/<service>/
```

## Mitigation

**If the probe port is wrong**: Patch the port to match the actual service port.
```bash
kubectl patch deployment <name> -n <namespace> --type='json' \
  -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/httpGet/port", "value": <correct_port>}]'
```

**If the probe uses httpGet against a gRPC service** (or the source manifest has no probe): Remove the probe entirely.
```bash
kubectl patch deployment <name> -n <namespace> --type='json' \
  -p='[{"op": "remove", "path": "/spec/template/spec/containers/0/livenessProbe"}]'
```

**If the probe timing is too aggressive** (Pattern B): Restore proper `initialDelaySeconds`, `failureThreshold`, and `periodSeconds` from the source manifest.
```bash
kubectl patch deployment <name> -n <namespace> --type='json' -p='[
  {"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/initialDelaySeconds", "value": 60},
  {"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/failureThreshold", "value": 3},
  {"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/periodSeconds", "value": 10}
]'
```
The correct values should come from the source manifest (e.g. `.local_tmp/*.yaml` or `kubernetes/<service>/*.yaml`). The `initialDelaySeconds` must be greater than the service's startup time.

**Note**: gRPC services cannot use `httpGet` probes. Kubernetes 1.24+ supports `grpc` probe type natively; older clusters need an exec-based grpc_health_probe approach. If the source manifest has no liveness probe, simply remove the injected one.

## Verification

After patching, confirm the pod stays Running without being killed:
```bash
kubectl get pods -n <namespace> -w  # watch for stability
kubectl describe pod -n <namespace> <pod-name>  # no more "Liveness probe failed" events
```
