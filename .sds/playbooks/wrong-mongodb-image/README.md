# wrong-mongodb-image

## Symptom

Multiple MongoDB pods in `Error` or `CrashLoopBackOff` from cluster start. Non-MongoDB pods may still be Running.

## Key Signal

MongoDB pod logs contain:

```
"s":"F", "c":"CONTROL", "id":20573, "ctx":"initandlisten",
"msg":"Wrong mongod version",
"error":"UPGRADE PROBLEM: Found an invalid featureCompatibilityVersion document ...
version: \"4.4\" ... expected '7.0' or '7.3' or '8.0'"
```

Exit code 62.

## Root Cause

The MongoDB deployment image was changed to a major version that is incompatible with the data on the PVC. MongoDB refuses to open data files whose `featureCompatibilityVersion` is more than one major version behind the running binary. Common injection: bumping `mongo:4.4.6` to `mongo:8.0.x` while leaving existing PVCs intact.

## Diagnosis

```bash
# Check which MongoDB pods are failing
kubectl get pods -n hotel-reservation | grep mongodb

# Check previous container logs for the FCV error
kubectl logs -n hotel-reservation <mongodb-pod> --previous 2>&1 | grep -E "featureCompatibilityVersion|Wrong mongod|UPGRADE PROBLEM"

# Confirm what image is running vs what source manifests specify
kubectl get deployments -n hotel-reservation -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.template.spec.containers[0].image}{"\n"}{end}' | grep mongo
```

If the running image major version is more than 1 major version ahead of the stored FCV, this playbook applies.

## Mitigation

Restore the MongoDB image to the version matching the stored data. Source manifests are authoritative:

```bash
# Check source manifest image
grep "image:" SREGym-applications/hotelReservation/kubernetes/geo/mongodb-geo-deployment.yaml

# Restore all MongoDB deployments to the correct image
CORRECT_IMAGE="mongo:4.4.6"  # confirm from source manifests
for svc in geo profile rate recommendation reservation user; do
  kubectl set image deployment/mongodb-$svc \
    "$(kubectl get deployment/mongodb-$svc -n hotel-reservation -o jsonpath='{.spec.template.spec.containers[0].name}')=${CORRECT_IMAGE}" \
    -n hotel-reservation
done
```

The Recreate strategy means old pods are terminated before new ones start — rollout takes ~10-20 seconds per deployment.

## Verification

```bash
# All MongoDB pods should reach Running/Ready within ~30s
kubectl get pods -n hotel-reservation | grep mongodb

# Confirm services and dependent app pods are healthy
kubectl get pods -n hotel-reservation
kubectl get endpoints -n hotel-reservation | grep mongodb

# End-to-end: search flow exercises geo, rate, profile, recommendation
kubectl run curl-test -n hotel-reservation --restart=Never --image=curlimages/curl:latest --command -- \
  curl -fsS -o /dev/null -w "%{http_code}" \
  "http://frontend:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7749&lon=-122.4194&userID=user1"
kubectl logs curl-test -n hotel-reservation
kubectl delete pod curl-test -n hotel-reservation --ignore-not-found
```

## Notes

- This fault affects all MongoDB deployments simultaneously since all PVCs were initialized with the same MongoDB version.
- `mongodb-recommendation` may appear to survive longer if its startup race self-heals, but check its image too.
- Do NOT try to upgrade MongoDB in-place through intermediate versions in a test cluster; simply roll back the image.
