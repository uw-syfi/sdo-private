# MongoDB Access Revoked

## Symptom

An application service pod crashes (CrashLoopBackOff / Error) at startup, or fails requests at runtime, with a MongoDB authorization error in the logs:

```
not authorized on <db-name> to execute command { ... }
```

The service can connect to MongoDB (session opens successfully) but cannot read or write to its database.

## Root Cause

The MongoDB `admin` user (used by Hotel Reservation services) has had its `readWrite` role revoked from the service's database (e.g. `geo-db`, `rate-db`). This is a live MongoDB privilege change — the deployment image and manifests are correct.

**Key signal**: `failure-admin-<service>` ConfigMap exists in the namespace. Its `revoke-admin-<service>-mongo.sh` script documents the injected fault (revoking `readWrite` on `<service>-db`).

## Diagnosis

1. Check pod logs for the auth error:
   ```bash
   kubectl logs -n hotel-reservation -l io.kompose.service=<service> --previous
   ```
2. Confirm the ConfigMap is present:
   ```bash
   kubectl get configmap -n hotel-reservation | grep failure-admin
   ```
3. Confirm the role is missing on the MongoDB pod:
   ```bash
   kubectl exec -n hotel-reservation deploy/mongodb-<service> -- \
     mongo admin -u admin -p admin --authenticationDatabase admin \
     --eval "db.getUser('admin')"
   ```
   The `roles` array should contain `{role: "readWrite", db: "<service>-db"}`. If it is absent, the fault is confirmed.

## Mitigation

Grant the `readWrite` role back to the `admin` user:

```bash
kubectl exec -n hotel-reservation deploy/mongodb-<service> -- \
  mongo admin -u admin -p admin --authenticationDatabase admin \
  --eval "db.grantRolesToUser('admin', [{role: 'readWrite', db: '<service>-db'}]);"
```

The pod will recover on its next CrashLoopBackOff restart. Verify:

```bash
kubectl get pod -n hotel-reservation -l io.kompose.service=<service>
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
- A service that started *before* the revocation (e.g. rate initialized DB data before the role was revoked) may appear healthy but could silently fail on cache-miss writes. Verify with the end-to-end test.
