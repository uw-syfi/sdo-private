# Injected Scheduling Constraint

## Symptom

One or more pods stuck in `Pending` state from cluster start. No node can satisfy the scheduling request.

Common `kubectl describe pod` events:
- `0/N nodes are available: N Insufficient memory` — absurdly large memory request injected
- `0/N nodes didn't have free ports for the requested pod ports` — `hostPort` injected that conflicts with already-used ports

## Cause

A fault injection modifies a deployment spec to add a field that makes the pod unschedulable:

1. **Injected memory request**: an impossibly large `resources.requests.memory` (e.g. `540478684528640m` ≈ 540TB) is added to a container, exceeding all available node memory.
2. **Injected hostPort**: a `hostPort` field is added to a container port, binding it to a specific host port that is already occupied on all worker nodes.

These are live cluster mutations — the source manifest may be clean. Always compare against the source before patching.

## Diagnosis

```bash
# 1. Identify pending pods
kubectl get pods -n <namespace> | grep Pending

# 2. Check scheduling failure reason
kubectl describe pod -n <namespace> <pod-name> | grep -A5 "Events:"

# 3. For "Insufficient memory" — check memory request
kubectl get deployment -n <namespace> <deploy> -o jsonpath='{.spec.template.spec.containers[0].resources}'

# 4. For "free ports" — check hostPort
kubectl get deployment -n <namespace> <deploy> -o jsonpath='{.spec.template.spec.containers[0].ports}'
```

Or use the diagnose script:

```bash
bash .sds/playbooks/injected-scheduling-constraint/scripts/diagnose.sh <namespace>
```

## Mitigation

### Remove injected memory request

```bash
kubectl patch deployment -n <namespace> <deploy> --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/containers/0/resources/requests/memory"}]'
```

### Remove injected hostPort

```bash
kubectl patch deployment -n <namespace> <deploy> --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/containers/0/ports/0/hostPort"}]'
```

If the port appears at a different index, check with:
```bash
kubectl get deployment -n <namespace> <deploy> -o jsonpath='{.spec.template.spec.containers[0].ports}'
```

## Verification

```bash
kubectl get pods -n <namespace>       # all pods Running
# end-to-end check
kubectl exec -n <namespace> deploy/frontend -- wget -qO- http://localhost:5000/
```

Or use the verify script:
```bash
bash .sds/playbooks/injected-scheduling-constraint/scripts/verify.sh <namespace>
```
