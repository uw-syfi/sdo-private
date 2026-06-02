# missing-service

**Symptom:** A microservice pod is in `CrashLoopBackOff`; logs show `no reachable servers` (or similar DNS/connection failure) for a dependency hostname that matches an expected internal service name (e.g. `mongodb-rate:27017`). The dependency's Pod and Deployment are healthy, but `kubectl get svc` shows no matching Service, and `kubectl get endpoints` shows no entry for that hostname.

## Diagnosis

1. Identify the crashing pod and read its logs:
   ```bash
   kubectl logs -n hotel-reservation deploy/<service> --previous
   ```
   Look for: connection panic naming an internal hostname (e.g. `mongodb-rate:27017`).

2. Check whether a Service exists for that hostname:
   ```bash
   kubectl get svc -n hotel-reservation | grep <dependency-name>
   kubectl get endpoints -n hotel-reservation | grep <dependency-name>
   ```
   If the Service is absent but the Deployment/Pod is present and Running, the Service was not deployed.

3. Confirm the Pod exists and is healthy:
   ```bash
   kubectl get pods -n hotel-reservation | grep <dependency-name>
   ```

4. Check source-controlled manifests for the missing Service:
   ```bash
   find /mnt/data/shli/sds/bench/sregym/SREGym-applications -name "<dependency-name>-service.yaml"
   ```

## Mitigation

Apply the missing Service from source:
```bash
kubectl apply -f /mnt/data/shli/sds/bench/sregym/SREGym-applications/hotelReservation/knative/<dependency-name>-service.yaml -n hotel-reservation
```

Or create manually (using mongodb-rate as example):
```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Service
metadata:
  labels:
    io.kompose.service: mongodb-rate
  name: mongodb-rate
  namespace: hotel-reservation
spec:
  ports:
  - name: mongodb-rate
    port: 27017
    targetPort: 27017
  selector:
    io.kompose.service: mongodb-rate
  type: ClusterIP
EOF
```

The crashing microservice will reconnect on its next restart cycle (CrashLoopBackOff back-off may add a brief delay).

## Verification

```bash
# 1. Endpoint is populated
kubectl get endpoints <dependency-name> -n hotel-reservation

# 2. Dependent pod recovers
kubectl get pods -n hotel-reservation | grep <service-name>

# 3. End-to-end flow works
kubectl run curl-test -n hotel-reservation --restart=Never --image=curlimages/curl:latest \
  --command -- curl -fsS http://frontend:5000/hotels?inDate=2015-04-09\&outDate=2015-04-10\&lat=37.7867\&lon=-122.4112
kubectl logs curl-test -n hotel-reservation
kubectl delete pod curl-test -n hotel-reservation --ignore-not-found
```

## Root Cause Pattern

The Deployment (and its Pod) was deployed but the corresponding Service manifest was skipped or never applied. In the hotelReservation app, each stateful dependency (mongodb-*, memcached-*) requires both a Deployment **and** a Service resource. If only the Deployment is created, the pod runs fine but no DNS entry exists for the hostname the application uses, causing an immediate connection panic at startup.

The source-controlled Service manifests live in:
`/mnt/data/shli/sds/bench/sregym/SREGym-applications/hotelReservation/knative/<name>-service.yaml`
