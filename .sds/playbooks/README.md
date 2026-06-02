# SDS Playbooks — Routing Index

Quick reference for diagnosing and mitigating recurring fault classes in this environment.

| Playbook | Symptom | Key Signal |
|----------|---------|------------|
| [wrong-dns-policy](wrong-dns-policy/README.md) | Pod CrashLoopBackOff; logs show DNS lookup failure for internal service names using a public resolver (e.g. `8.8.8.8`) | `dnsPolicy: None` + external nameserver in deployment spec |
| [wrong-liveness-probe](wrong-liveness-probe/README.md) | Pod repeatedly killed and restarted despite running; liveness probe fails | Liveness probe path/port mismatch vs actual service endpoint, or httpGet probe on a gRPC port (malformed HTTP response) |
| [wrong-memory-limit](wrong-memory-limit/README.md) | Pod in OOMKilled loop; dependent service loses endpoint | Memory limit injected far below process working set (e.g. 10Mi for MongoDB); `kubectl get endpoints` shows `<none>` |
| [missing-configmap](missing-configmap/README.md) | Pod stuck in `CreateContainerConfigError`; missing configmap volume mount | `configmap not found` in pod events |
| [mongodb-startup-race](mongodb-startup-race/README.md) | App service pod crashes immediately at startup; MongoDB connection refused | Service starts before MongoDB is ready; pod logs show connection error |
| [wrong-container-command](wrong-container-command/README.md) | Pod in `CrashLoopBackOff`; `exec: "<binary>": executable file not found` | Container command references wrong binary name for the image |
| [mongodb-access-revoked](mongodb-access-revoked/README.md) | App pod CrashLoopBackOff at startup (or runtime auth failures); logs show `not authorized on <db> to execute command` | `failure-admin-<svc>` ConfigMap present; `db.getUser('admin')` missing readWrite role on service DB |
| [injected-init-container](injected-init-container/README.md) | Pod stuck in `Init:0/1` forever; init container hangs | Init container added to deployment that blocks main container start |
| [broken-pvc-claimname](broken-pvc-claimname/README.md) | Stateful pod (e.g. MongoDB) stuck in `Pending`; dependent app pods CrashLoopBackOff with "no reachable servers" | `kubectl describe pod` shows PVC `<name>-broken` not found; actual PVCs exist and are Bound |

## Usage

Each playbook has:
- `README.md` — diagnosis + mitigation guide
- `scripts/diagnose.sh` — focused symptom checker (optional)
- `scripts/mitigate.sh` — reusable mitigation helper (optional)
- `scripts/verify.sh` — post-fix verification (optional)
