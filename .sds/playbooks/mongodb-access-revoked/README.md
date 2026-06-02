# MongoDB Access Revoked

## Symptom

An application service pod crashes (CrashLoopBackOff / Error) at startup with a MongoDB authentication or authorization error in the logs:

- **Variant A — role revoked**: `not authorized on <db-name> to execute command { ... }` — user connects but has no readWrite permission
- **Variant B — user deleted**: `server returned error on SASL authentication step: Authentication failed.` — user does not exist at all

## Root Cause

The MongoDB `admin` user (used by Hotel Reservation services) has been tampered with in one of two ways:

- **Variant A**: `readWrite` role revoked from the service's database (e.g. `rate-db`, `geo-db`)
- **Variant B**: `admin` user deleted entirely from the `admin` database

This is a live MongoDB state change — deployment image and manifests are correct.

**Key signal**: `failure-admin-<service>` ConfigMap exists in the namespace. Its scripts (`revoke-admin-*.sh`, `remove-admin-mongo.sh`) document which fault variant was injected.

## Diagnosis

1. Check pod logs for the error type:
   ```bash
   kubectl logs -n hotel-reservation -l io.kompose.service=<service> --previous
   ```
   - "Authentication failed" → Variant B (user deleted)
   - "not authorized" → Variant A (role revoked)

2. Confirm the ConfigMap is present:
   ```bash
   kubectl get configmap -n hotel-reservation | grep failure-admin
   ```

3. Check what users exist in MongoDB (use `root`/`root` if `admin`/`admin` fails):
   ```bash
   kubectl exec -n hotel-reservation deploy/mongodb-<service> -- \
     mongo admin -u root -p root --authenticationDatabase admin \
     --eval "db.getUsers()"
   ```
   - If `admin` user is absent → Variant B
   - If `admin` user exists but lacks `{role: "readWrite", db: "<service>-db"}` → Variant A

## Mitigation

### Variant A — Restore readWrite role

```bash
kubectl exec -n hotel-reservation deploy/mongodb-<service> -- \
  mongo admin -u admin -p admin --authenticationDatabase admin \
  --eval "db.grantRolesToUser('admin', [{role: 'readWrite', db: '<service>-db'}]);"
```

### Variant B — Recreate deleted admin user

```bash
kubectl exec -n hotel-reservation deploy/mongodb-<service> -- \
  mongo admin -u root -p root --authenticationDatabase admin \
  --eval "db.createUser({user: 'admin', pwd: 'admin', roles:[{role:'userAdminAnyDatabase',db:'admin'}]});
          db.grantRolesToUser('admin', [{role: 'readWrite', db: '<service>-db'}]);"
```

After either fix, restart the deployment so it picks up fresh credentials:

```bash
kubectl rollout restart deployment/<service> -n hotel-reservation
kubectl rollout status deployment/<service> -n hotel-reservation
```

## Verification

Run the full end-to-end health check:

```bash
kubectl run curl-test -n hotel-reservation --restart=Never --image=curlimages/curl:latest \
  --command -- curl -fsS \
  "http://frontend:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7749&lon=-122.4194&customerName=test_user_0"
kubectl logs -n hotel-reservation curl-test
kubectl delete pod curl-test -n hotel-reservation --ignore-not-found
```

Expect a JSON `FeatureCollection` with hotel results.

## Stealth variant: services survive revocation if pods aren't restarted

The fault injection sequence always:
1. Runs the revoke script inside the MongoDB pod
2. **Deletes the service pod** to force a restart that will fail

Without step 2, the service continues serving from warm state:
- **geo**: loads all hotels into an in-memory index at startup — never queries MongoDB during request handling
- **rate**: caches per-hotel rate plans in memcached — only queries MongoDB on cache misses; once the cache is warm (~seconds after startup) there are zero cache misses

**Tell**: If `failure-admin-<svc>` ConfigMap is present but `admin` still has `readWrite` in MongoDB AND the service pod creation timestamp matches the original cluster deployment time (not a later date), the revoke script likely ran but the pod was never deleted (fault injection aborted midway, or scripts not executed at all).

Check pod creation timestamps to discriminate:
```bash
kubectl get pods -n hotel-reservation -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.creationTimestamp}{"\n"}{end}' | sort
```
If the `geo` or `rate` pod was re-created *after* the cluster started (later timestamp than mongodb pods), it was deleted as part of fault injection. If all pods share the same ~2-second creation window, no pod was deleted — the fault is either not yet applied or was applied only at the MongoDB level.

Confirm via MongoDB audit log (localhost connections = kubectl exec / scripts):
```bash
kubectl logs -n hotel-reservation deploy/mongodb-rate | grep "revokeRoles\|ACCESS"
```
`revokeRolesFromUser` entries in the log confirm the script ran; their absence means it did not.

## Notes

- The geo and rate services hard-code `admin:admin` credentials in `cmd/geo/db.go` and `cmd/rate/db.go` respectively.
- If both `failure-admin-geo` and `failure-admin-rate` ConfigMaps exist, check both databases — the fault may be injected on multiple services simultaneously.
- A service that started *before* the deletion/revocation may appear healthy but could silently fail on later writes. Always verify end-to-end.
- The `root`/`root` user always remains present and can be used as a recovery pivot when `admin`/`admin` no longer works.
- `configmap present + readWrite intact + pod age matches cluster start` = fault prepared but not applied (or fully recovered).
