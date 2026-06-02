# Duplicate PVC Mounts (Scheduling Deadlock)

## Symptom

One pod in a multi-replica deployment is stuck in `Pending` from cluster start.
Scheduler events contain **both** of:
- `pod anti-affinity rules` — a required anti-affinity prevents two pods from landing on the same node
- `volume node affinity conflict` or `available persistent volumes to bind` — the PVC is a RWO hostpath volume already bound to a specific node

The first replica schedules and runs; the second is permanently unschedulable.

## Cause

A fault injection modifies a deployment to create an unsolvable scheduling contradiction:

1. **RWO PVC added** — a new `{service}-pvc` (ReadWriteOnce, openebs-hostpath) is created and mounted into the container. Because openebs-hostpath is node-local, the PVC is only accessible from the node it was provisioned on.
2. **podAntiAffinity injected** — a `requiredDuringSchedulingIgnoredDuringExecution` rule (topologyKey: `kubernetes.io/hostname`) forces each replica onto a different node.
3. **Replica count bumped to ≥ 2** — guarantees at least two replicas must schedule.

With two replicas: pod A runs on the node where the PVC lives, pod B is blocked by anti-affinity from joining that node and blocked by volume node affinity from running anywhere else.

## Diagnosis

```bash
# 1. Find pending pods
kubectl get pods -n <namespace> | grep Pending

# 2. Confirm the two-part scheduling failure
kubectl describe pod -n <namespace> <pending-pod> | grep -A5 "Events:"
# Look for: "pod anti-affinity rules" AND "volume node affinity conflict"

# 3. Confirm an injected PVC was attached
kubectl get deployment -n <namespace> <deploy> -o jsonpath='{.spec.template.spec.volumes}'
# Look for a {service}-pvc claim that does not exist in the source manifests

# 4. Confirm anti-affinity is present (should not be in source)
kubectl get deployment -n <namespace> <deploy> -o jsonpath='{.spec.template.spec.affinity}'
```

Or use the diagnose script:
```bash
bash .sds/playbooks/duplicate-pvc-mounts/scripts/diagnose.sh hotel-reservation
```

## Mitigation

Patch the deployment to remove the injected anti-affinity, PVC volume, container volumeMount, and scale back to 1 replica (the source-controlled value):

```bash
kubectl patch deployment -n <namespace> <deploy> --type=json -p='[
  {"op": "remove", "path": "/spec/template/spec/affinity"},
  {"op": "remove", "path": "/spec/template/spec/volumes"},
  {"op": "remove", "path": "/spec/template/spec/containers/0/volumeMounts"},
  {"op": "replace", "path": "/spec/replicas", "value": 1}
]'

# Clean up the orphaned PVC
kubectl delete pvc -n <namespace> <service>-pvc
```

Or use the mitigate script:
```bash
bash .sds/playbooks/duplicate-pvc-mounts/scripts/mitigate.sh hotel-reservation frontend
```

> **Note**: If the container had pre-existing volumeMounts (e.g. configMap mounts), removing the entire array is wrong — remove only the injected entry by index instead. Check source manifest first.

## Verification

```bash
kubectl get pods -n <namespace>           # all Running, no Pending
kubectl get deployment -n <namespace> <deploy>   # READY = 1/1
kubectl exec -n <namespace> deploy/<deploy> -- wget -qO- http://localhost:5000/
```

Or use the verify script:
```bash
bash .sds/playbooks/duplicate-pvc-mounts/scripts/verify.sh hotel-reservation
```
