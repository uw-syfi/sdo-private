# SDS Playbooks — Routing Index

Quick reference for diagnosing and mitigating recurring fault classes in this environment.

| Playbook | Symptom | Key Signal |
|----------|---------|------------|
| [wrong-dns-policy](wrong-dns-policy/README.md) | Pod CrashLoopBackOff; logs show DNS lookup failure for internal service names using a public resolver (e.g. `8.8.8.8`) | `dnsPolicy: None` + external nameserver in deployment spec |
| [wrong-liveness-probe](wrong-liveness-probe/README.md) | Pod repeatedly killed and restarted; liveness probe fails — either immediately (CrashLoopBackOff within seconds) or after startup | **Pattern A** (wrong target): probe error shows connection refused / malformed HTTP / 404. **Pattern B** (too-aggressive timing): `statuscode: 503` in probe event + service has init delay; `initialDelaySeconds=0, failureThreshold=1` kills pod before it can initialize |
| [wrong-memory-limit](wrong-memory-limit/README.md) | Pod in OOMKilled loop; dependent service loses endpoint | Memory limit injected far below process working set (e.g. 10Mi for MongoDB); `kubectl get endpoints` shows `<none>` |
| [missing-configmap-key](missing-configmap-key/README.md) | Pod starts briefly then crashes; logs show empty value read for required config key (e.g. DB address), then connection panic | `"Read database URL: "` (blank) + `no reachable servers` in pod logs; ConfigMap mounts fine but a key is absent from its JSON |
| [mongodb-startup-race](mongodb-startup-race/README.md) | App service pod crashes immediately at startup; MongoDB connection refused | Service starts before MongoDB is ready; pod logs show connection error |
| [wrong-container-command](wrong-container-command/README.md) | Pod in `CrashLoopBackOff`; `exec: "<binary>": executable file not found` | Container command references wrong binary name for the image |
| [mongodb-access-revoked](mongodb-access-revoked/README.md) | App pod CrashLoopBackOff; logs show `Authentication failed` (user deleted) or `not authorized on <db>` (role revoked) | `failure-admin-<svc>` ConfigMap present; check `db.getUsers()` via `root`/`root` — admin user missing (Variant B) or lacks readWrite role (Variant A) |
| [injected-init-container](injected-init-container/README.md) | Pod stuck in `Init:0/1` forever; init container hangs | Init container added to deployment that blocks main container start |
| [broken-pvc-claimname](broken-pvc-claimname/README.md) | Stateful pod (e.g. MongoDB) stuck in `Pending`; dependent app pods CrashLoopBackOff with "no reachable servers"; after recovery frontend may still panic with nil pointer in geoJSONResponse (corrupted memcached cache) | `kubectl describe pod` shows PVC `<name>-broken` not found; actual PVCs exist and are Bound. May have compound faults: extra injected volumes and/or nodeSelector on same deployment. Post-recovery: flush memcached if profile service cached empty hotels during outage |
| [injected-scheduling-constraint](injected-scheduling-constraint/README.md) | Pod(s) stuck in `Pending` from cluster start; `Insufficient memory` or `no free ports` in scheduler events | Injected absurd `resources.requests.memory` or `hostPort` in deployment spec |
| [duplicate-pvc-mounts](duplicate-pvc-mounts/README.md) | One pod of a multi-replica deployment stuck in `Pending`; first replica runs, second cannot schedule | Scheduler events show both `anti-affinity rules` AND `volume node affinity conflict`; deployment has injected RWO PVC + `podAntiAffinity` |
| [wrong-mongodb-image](wrong-mongodb-image/README.md) | All MongoDB pods in `Error`/`CrashLoopBackOff` from cluster start; app services crash connecting to DB | Pod logs: `"Wrong mongod version" ... featureCompatibilityVersion: "4.4"` — MongoDB image major version incompatible with PVC data |
| [wrong-service-command](wrong-service-command/README.md) | Pod Running/Ready but RPC calls to it fail; upstream logs show `GetX failed` 500 errors; consul has no entry for the expected service | `kubectl describe deployment <name>` shows wrong `Command:` (e.g. `geo` in `profile` deployment); pod logs show wrong `cmd/<other>/main.go` and wrong consul registration |
| [injected-service-selector](injected-service-selector/README.md) | All traffic to a service fails ("Connection refused"); pods are Running/Ready but `kubectl get endpoints <svc>` shows empty | Extra label key in `.spec.selector` that pods do not carry; selector ANDs to zero matches |
| [missing-service](missing-service/README.md) | Microservice pod CrashLoopBackOff; logs show `no reachable servers` for an internal hostname; the dependency Deployment/Pod are healthy but `kubectl get svc` has no entry | `kubectl get svc \| grep <dep-name>` returns nothing; `kubectl get endpoints \| grep <dep-name>` returns nothing — Service was never deployed |
| [namespace-memory-quota](namespace-memory-quota/README.md) | Cluster looks healthy (all pods Running) but new pods for any deployment without memory requests cannot be created; `search` pod may be missing after its ReplicaSet was deleted | `kubectl get resourcequota -n hotel-reservation` shows `memory-limit-quota`; new pod creation fails with `memory-limit-quota: must specify memory for: <pod>` |

## Usage

Each playbook has:
- `README.md` — diagnosis + mitigation guide
- `scripts/diagnose.sh` — focused symptom checker (optional)
- `scripts/mitigate.sh` — reusable mitigation helper (optional)
- `scripts/verify.sh` — post-fix verification (optional)
