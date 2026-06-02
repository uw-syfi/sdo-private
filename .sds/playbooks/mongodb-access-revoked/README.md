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

## Notes

- The geo and rate services hard-code `admin:admin` credentials in `cmd/geo/db.go` and `cmd/rate/db.go` respectively.
- If both `failure-admin-geo` and `failure-admin-rate` ConfigMaps exist, check both databases — the fault may be injected on multiple services simultaneously.
- A service that started *before* the deletion/revocation may appear healthy but could silently fail on later writes. Always verify end-to-end.
- The `root`/`root` user always remains present and can be used as a recovery pivot when `admin`/`admin` no longer works.
