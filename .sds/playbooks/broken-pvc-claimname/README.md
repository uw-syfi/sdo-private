# Broken PVC ClaimName — MongoDB Pods Stuck Pending

## Symptom

One or more stateful service pods (e.g. MongoDB) are stuck in `Pending` state indefinitely. Dependent application pods enter `CrashLoopBackOff` with errors like `panic: no reachable servers` or `connection refused`. No node scheduling events succeed.

## Root Cause

One or more deployments reference PersistentVolumeClaims (PVCs) that do not exist. The `claimName` field in the deployment volume spec has been changed (e.g. `geo-pvc` → `geo-pvc-broken`) to point to a non-existent PVC. Kubernetes cannot schedule the pod because the volume cannot be bound.

Additionally, a deployment may have compound injected faults:
- A `nodeSelector` that conflicts with the PV's node affinity (scheduling conflict even if PVC name is fixed)
- Extra injected volumes referencing unrelated PVCs not present in the source manifest

The fault is injected into the live cluster state — source manifests remain correct.

## Diagnosis

```bash
# Check for Pending pods
kubectl get pods -n <namespace> | grep Pending

# Check pod events for volume errors
kubectl describe pod -n <namespace> <pod-name>
# Look for: "persistentvolumeclaim "<name>-broken" not found"

# List actual PVCs that exist and are Bound
kubectl get pvc -n <namespace>

# Check what claimName a deployment references
kubectl get deployment <svc> -n <namespace> \
  -o jsonpath='{.spec.template.spec.volumes[*].persistentVolumeClaim.claimName}'

# Check for nodeSelector conflicts
kubectl get deployment <svc> -n <namespace> \
  -o jsonpath='{.spec.template.spec.nodeSelector}'

# Compare to source manifest
diff <(kubectl get deployment <svc> -n <namespace> -o yaml) \
     kubernetes/<svc>/mongodb-<svc>-deployment.yaml
```

**Tell**: `kubectl describe pod` on a Pending MongoDB pod shows `persistentvolumeclaim "<name>-broken" not found` in events. `kubectl get pvc -n <namespace>` shows the correct PVCs exist and are Bound — only the referenced names are wrong.

## Mitigation

### Simple case: only claimName is broken

Patch the deployment to restore the correct PVC name:

```bash
kubectl patch deployment -n <namespace> mongodb-<svc> --type=json \
  -p='[{"op": "replace", "path": "/spec/template/spec/volumes/0/persistentVolumeClaim/claimName", "value": "<svc>-pvc"}]'
```

For multiple deployments at once (hotel-reservation pattern):

```bash
for svc in geo profile rate recommendation reservation user; do
  kubectl patch deployment -n hotel-reservation mongodb-${svc} --type=json \
    -p="[{\"op\": \"replace\", \"path\": \"/spec/template/spec/volumes/0/persistentVolumeClaim/claimName\", \"value\": \"${svc}-pvc\"}]"
done
```

### Compound case: additional injected faults (nodeSelector, extra volumes)

Re-apply the clean source manifest to overwrite all injected fields:

```bash
kubectl apply -f kubernetes/<svc>/mongodb-<svc>-deployment.yaml -n <namespace>
```

Or use the helper script:

```bash
.sds/playbooks/broken-pvc-claimname/scripts/mitigate.sh <namespace> <svc>
```

## Secondary Effect: Corrupted Memcached Cache

When MongoDB is unavailable long enough for services to process requests, caching layers (e.g. memcached-profile) may accumulate corrupted entries. In hotel-reservation, the profile service appends empty hotel structs (with nil `Address`) even on MongoDB errors and writes them to memcached. After MongoDB recovers, cached lookups return the corrupt empty structs, causing nil-pointer panics in the frontend's `geoJSONResponse`.

**Tell**: After MongoDB pods recover, the frontend still panics with `nil pointer dereference` in `geoJSONResponse` / `server.go:412` even though all pods are Running.

**Fix**: Restart memcached-profile (or any other memcached used by DB-backed services) to flush stale entries. The service will repopulate the cache from the now-healthy MongoDB.

```bash
kubectl rollout restart deployment/memcached-profile -n <namespace>
# After memcached restarts, the profile service should reconnect and recover automatically.
# Verify with:
kubectl exec -n <namespace> deploy/frontend -- curl -fsS \
  "http://localhost:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7749&lon=-122.4194"
```

## Verification

```bash
# All MongoDB pods should reach Running
kubectl get pods -n <namespace> | grep mongodb

# All PVC endpoints should be populated
kubectl get endpoints -n <namespace>

# Application pod should recover (may take 1-2 min for CrashLoopBackOff backoff)
kubectl get pods -n <namespace>

# End-to-end hotel search (verifies geo + reservation + profile chain)
kubectl exec -n <namespace> deploy/frontend -- curl -fsS \
  "http://localhost:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7749&lon=-122.4194"

# End-to-end recommendations, login, reservation
kubectl exec -n <namespace> deploy/frontend -- curl -fsS \
  "http://localhost:5000/recommendations?require=rate&lat=37.7749&lon=-122.4194"
kubectl exec -n <namespace> deploy/frontend -- curl -fsS -X POST \
  "http://localhost:5000/user?username=Cornell_1&password=1111111111"
kubectl exec -n <namespace> deploy/frontend -- curl -fsS -X POST \
  "http://localhost:5000/reservation?inDate=2015-04-11&outDate=2015-04-12&hotelId=1&customerName=Cornell_1&username=Cornell_1&password=1111111111&number=1"
```

## Notes

- The volume index (`/spec/template/spec/volumes/0/...`) in the JSON patch path assumes the PVC volume is at index 0. Verify with `kubectl get deployment -o json` if there are multiple volumes.
- When removing an extra injected volume, also remove its corresponding `volumeMount` from the container spec — the patch will fail validation if a volumeMount references a volume that no longer exists.
- A `nodeSelector` conflict with PV node affinity will keep the pod Pending even after the PVC name is fixed. Always check both constraints.
- Source manifests are clean — this is a live-cluster-only injection. Re-applying the source manifest is always a safe fallback for compound faults.
- After fixing MongoDB pods, application pods in CrashLoopBackOff will self-recover once their MongoDB backend is reachable (Kubernetes exponential backoff — may take 1-5 minutes).
- Always flush memcached for DB-backed services after a prolonged MongoDB outage. Cached empty/corrupt structs survive pod restarts and cause downstream panics even when MongoDB is healthy again.
