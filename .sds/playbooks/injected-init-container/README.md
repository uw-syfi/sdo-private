# Injected Init Container

## Symptom

One or more pods are stuck in `Init:0/1` (or `Init:N/M`) state indefinitely. The deployment itself is progressing but pods never reach Running/Ready.

## Root Cause

An init container was injected into the deployment spec that intentionally hangs (e.g. `sleep infinity`), preventing the main container from ever starting. The pod's main container will stay in `PodInitializing` / `Waiting` state until the init container exits or is removed.

**Key signal**: `kubectl describe pod <pod>` shows an init container (e.g. `hang-init`) in `Running` state with no termination condition, and the main container in `PodInitializing`.

## Diagnosis

```bash
# 1. Identify stuck pods
kubectl get pods -n hotel-reservation | grep -E "Init:"

# 2. Inspect the pod to find the init container
kubectl describe pod -n hotel-reservation <pod-name>
# Look for: Init Containers section with a command like "sleep infinity"

# 3. Confirm via the deployment spec
kubectl get deployment <name> -n hotel-reservation -o jsonpath='{.spec.template.spec.initContainers}'

# 4. Compare against last-applied-configuration to confirm injection
kubectl get deployment <name> -n hotel-reservation -o jsonpath='{.metadata.annotations.kubectl\.kubernetes\.io/last-applied-configuration}'
# If the last-applied-configuration has no initContainers, the init container was injected after deploy
```

Run the diagnose script:
```bash
.sds/playbooks/injected-init-container/scripts/diagnose.sh
```

## Mitigation

If the deployment's source manifest has no init containers (confirmed via `last-applied-configuration` or source repo), remove the injected init container:

```bash
kubectl patch deployment <name> -n hotel-reservation --type=json \
  -p='[{"op": "remove", "path": "/spec/template/spec/initContainers"}]'
```

If source manifests exist, fix the source file first, then re-apply:
```bash
# Edit the source manifest to remove initContainers, then:
kubectl apply -f <manifest-file>
```

After patching, the old stuck pods terminate and new pods start immediately without init containers.

## Verification

```bash
# Pods should transition to Running 1/1
kubectl get pods -n hotel-reservation -l app=<name>

# Confirm no initContainers in live spec
kubectl get deployment <name> -n hotel-reservation \
  -o jsonpath='{.spec.template.spec.initContainers}'
```

## Notes

- The `hang-init` init container pattern (busybox + `sleep infinity`) is a deliberate fault injection. The init container name and image may vary.
- The `last-applied-configuration` annotation shows what was originally deployed — compare it to the live spec to identify injected fields.
- This fault only affects new pods (rollouts, restarts). Existing Running pods from a prior ReplicaSet are not affected.
