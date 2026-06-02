# Namespace Memory Quota

## Symptom

A service deployment (typically `search`) fails to schedule new pods after a crash or rollout. Other deployments without memory requests also become unable to restart. The cluster may look healthy on the surface (all current pods Running/Ready), but pod replacement is silently broken.

Key signals:
- `kubectl get resourcequota -n hotel-reservation` shows a `memory-limit-quota` entry
- Attempting to create any pod without memory requests fails with: `memory-limit-quota: must specify memory for: <pod>`
- The search deployment may have recently been replaced (its ReplicaSet was deleted as part of fault injection)

## Root Cause

A `ResourceQuota` named `memory-limit-quota` is injected into the namespace with `hard: {memory: 1Gi}`. This quota:
1. Requires ALL new pods to specify `requests.memory`
2. Caps the total memory requests across all pods to 1Gi

The fault also deletes the search deployment's ReplicaSet, forcing a pod replacement. If the search deployment spec lacks memory requests, the replacement pod cannot be created, leaving the service unavailable.

## Diagnosis

```bash
# 1. Check for a memory ResourceQuota
kubectl get resourcequota -n hotel-reservation

# 2. Describe it to see utilization vs. hard limit
kubectl describe resourcequota memory-limit-quota -n hotel-reservation

# 3. Check if search (or another service) is missing its pod
kubectl get pods -n hotel-reservation | grep -v Running

# 4. Confirm the fault: verify no other deployment has memory requests
kubectl get deployments -n hotel-reservation -o json | \
  python3 -c "import json,sys; d=json.load(sys.stdin); \
    [print(i['metadata']['name'], [c.get('resources',{}) for c in i['spec']['template']['spec']['containers']]) \
    for i in d['items']]"
```

If `memory-limit-quota` is present and most deployments lack memory requests, this is the injected fault.

## Mitigation

### 1. Remove the ResourceQuota

```bash
kubectl delete resourcequota memory-limit-quota -n hotel-reservation
```

### 2. Ensure the search deployment has memory requests in source

If the search deployment's pod is missing (quota prevented it from starting), also add memory requests to the source manifest:

File: `SREGym-applications/hotelReservation/kubernetes/search/search-deployment.yaml`

```yaml
resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 1000m
    memory: 256Mi
```

Then apply:
```bash
kubectl apply -f SREGym-applications/hotelReservation/kubernetes/search/search-deployment.yaml
```

### 3. Verify search pod is Running/Ready

```bash
kubectl get pod -n hotel-reservation -l io.kompose.service=search
```

## Verification

```bash
# No memory quotas remain
kubectl get resourcequota -n hotel-reservation

# All pods Running/Ready
kubectl get pods -n hotel-reservation

# Search endpoint responds
kubectl exec -n hotel-reservation deploy/frontend -- \
  wget -qO- "http://localhost:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7867&lon=-122.4112"
```
