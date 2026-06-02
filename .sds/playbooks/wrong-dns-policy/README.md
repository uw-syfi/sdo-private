# Wrong DNS Policy — Service Name Resolution Failure

## Symptom

One or more pods are in `CrashLoopBackOff`. Logs show DNS lookup failures for internal service names (e.g., `lookup jaeger on 8.8.8.8:53: no such host`), or connection errors like `no reachable servers` (MongoDB Go driver does not expose DNS failure explicitly — it just fails to connect after the hostname cannot be resolved). The pod is using a public DNS resolver instead of the cluster's internal DNS.

## Root Cause

A deployment has been patched with:
- `spec.template.spec.dnsPolicy: None`
- `spec.template.spec.dnsConfig.nameservers: ["8.8.8.8"]` (or another public DNS)

This overrides Kubernetes' default `ClusterFirst` DNS policy, which routes `.cluster.local` and service names through `kube-dns`. With `dnsPolicy: None` and only public nameservers, pods cannot resolve in-cluster service names at all.

The fault is injected into the live deployment — not into the source manifest.

## Diagnosis

```bash
# Check pod DNS config
kubectl get pod -n <namespace> <pod-name> -o jsonpath='{.spec.dnsPolicy}{"\n"}{.spec.dnsConfig}'

# Check pod logs for DNS errors
kubectl logs -n <namespace> deploy/<service> --tail=30
# Look for: "lookup <service> on <public-ip>:53: no such host"

# Check deployment spec for injected dnsPolicy/dnsConfig
kubectl get deployment <svc> -n <namespace> -o jsonpath='{.spec.template.spec.dnsPolicy}'
kubectl get deployment <svc> -n <namespace> -o jsonpath='{.spec.template.spec.dnsConfig}'
```

**Tell**: `dnsPolicy: None` combined with an external nameserver like `8.8.8.8` is the discriminating signal. The source manifest will NOT contain these fields.

## Mitigation

Patch the deployment to remove the injected DNS overrides:

```bash
kubectl patch deployment <service> -n <namespace> --type=json -p='[
  {"op": "remove", "path": "/spec/template/spec/dnsPolicy"},
  {"op": "remove", "path": "/spec/template/spec/dnsConfig"}
]'
```

Or use the helper script:

```bash
.sds/playbooks/wrong-dns-policy/scripts/mitigate.sh <namespace> <deployment-name>
```

## Verification

```bash
kubectl get pods -n <namespace> -l io.kompose.service=<service>
kubectl get endpoints -n <namespace>
# Test a live request
kubectl exec -n <namespace> deploy/frontend -- wget -qO- http://localhost:5000/
```

## Notes

- The default `dnsPolicy` in Kubernetes is `ClusterFirst`, which routes cluster-local names through kube-dns. A missing `dnsPolicy` field in the deployment spec means `ClusterFirst` is used.
- The injected fault is a live cluster modification; source manifests will be clean.
- After removing the override, the pod will restart and resolve services normally.
