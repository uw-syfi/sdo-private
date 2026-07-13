#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cluster_name="${SDO_SMOKE_CLUSTER:-sdo-smoke}"
fixture="${repo_root}/tests/fixtures/sdo/missing_configmap_app"
kind_config="$(mktemp)"
job_manifest="$(mktemp)"

cleanup() {
  rm -f "${kind_config}" "${job_manifest}"
  kind delete cluster --name "${cluster_name}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

cat >"${kind_config}" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraMounts:
      - hostPath: ${fixture}
        containerPath: /fixtures/app
        readOnly: true
EOF

kind create cluster --name "${cluster_name}" --config "${kind_config}" --wait 120s
docker image inspect sdo-observer-validator:v0.1.0 >/dev/null
kind load docker-image sdo-observer-validator:v0.1.0 --name "${cluster_name}"

cat >"${job_manifest}" <<'EOF'
apiVersion: batch/v1
kind: Job
metadata:
  name: sdo-observer-validator-smoke
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      containers:
        - name: validator
          image: sdo-observer-validator:v0.1.0
          imagePullPolicy: Never
          command: ["python", "-m", "observer.updater.check_cli"]
          args: ["test", "--app", "/workspace"]
          env:
            - {name: HOME, value: /tmp}
            - {name: GOCACHE, value: /tmp/go-cache}
            - {name: GOMODCACHE, value: /go/pkg/mod}
            - {name: GOPROXY, value: "off"}
            - {name: GOSUMDB, value: "off"}
            - {name: GOMAXPROCS, value: "2"}
            - {name: GOFLAGS, value: "-p=2"}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: ["ALL"]}
          resources:
            limits: {cpu: "1", memory: 3Gi}
          volumeMounts:
            - {name: application, mountPath: /workspace, readOnly: true}
            - {name: scratch, mountPath: /tmp}
      volumes:
        - name: application
          hostPath: {path: /fixtures/app, type: Directory}
        - name: scratch
          emptyDir: {medium: Memory, sizeLimit: 3Gi}
EOF

kubectl apply -f "${job_manifest}"
if ! kubectl wait --for=condition=complete job/sdo-observer-validator-smoke --timeout=240s; then
  kubectl logs job/sdo-observer-validator-smoke || true
  exit 1
fi
kubectl logs job/sdo-observer-validator-smoke
