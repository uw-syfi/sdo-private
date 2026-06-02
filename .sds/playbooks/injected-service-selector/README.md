# injected-service-selector

## Symptom

All traffic to a service fails with "Connection refused" or timeout, even though the backing pod(s) are Running and Ready. `kubectl get endpoints <service>` shows an empty endpoint list (`<none>`).

The pod itself is healthy — it serves requests when reached directly — but the Service selector does not match it.

## Root Cause

An extra label requirement was injected into the Service's `.spec.selector`. Because Kubernetes ANDs all selector labels, adding any label that the pods do not carry causes the service to match zero pods and expose zero endpoints.

**Example:** source manifest has `selector: {io.kompose.service: frontend}`, but the live Service has `selector: {io.kompose.service: frontend, current_service_name: frontend}`. The pods only carry `io.kompose.service=frontend`, so the service has no endpoints.

## Diagnosis

1. Check endpoint list for the suspected service:
   ```bash
   kubectl get endpoints <svc> -n <namespace>
   ```
   If `ENDPOINTS` is blank, the selector is not matching any pods.

2. Compare the live selector against the pod labels:
   ```bash
   # Live selector
   kubectl get svc <svc> -n <namespace> -o jsonpath='{.spec.selector}'
   # Pod labels
   kubectl get pods -n <namespace> -l io.kompose.service=<svc> --show-labels
   ```
   Extra keys in the selector that are absent from pod labels confirm the fault.

3. Cross-check against the source manifest under `kubernetes/<service>/<service>-service.yaml` to identify the injected label(s).

## Mitigation

Remove the extra selector key(s) and restore the selector to the source-manifest values.

```bash
kubectl patch svc <svc> -n <namespace> --type=json \
  -p='[{"op": "replace", "path": "/spec/selector", "value": {"io.kompose.service": "<svc>"}}]'
```

Verify endpoints are immediately restored:
```bash
kubectl get endpoints <svc> -n <namespace>
```

## Verification

1. Endpoint is populated (`kubectl get endpoints`).
2. A direct request through the service ClusterIP or DNS name succeeds:
   ```bash
   kubectl exec -n <namespace> deploy/<any-pod> -- wget -qO- http://<svc>:<port>/
   ```
3. Load-test traffic (wrk2 or similar) shows `connect 0` socket errors and non-zero throughput.

## Notes

- This fault is injected at the Service layer, not the Deployment. The pod is healthy and there are no CrashLoopBackOff events.
- The label used in the injected selector (`current_service_name`) is not part of any standard Kompose or application convention in this repo — its presence on the Service selector is always suspicious.
- Fix the live Service with `kubectl patch` for immediate relief. If the same selector appears in the source manifest, correct it there too and re-apply so the fix survives the next deploy.
