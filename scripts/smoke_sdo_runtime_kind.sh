#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cluster_name="${SDO_RUNTIME_SMOKE_CLUSTER:-sdo-runtime-smoke}"
namespace="sdo-runtime-smoke"
workspace="$(mktemp -d)"
application="${workspace}/application"
calico_manifest="${workspace}/calico.yaml"
kind_config="${workspace}/kind.yaml"
calico_url="https://raw.githubusercontent.com/projectcalico/calico/v3.32.1/manifests/calico.yaml"
calico_sha256="a1df919d9721cf667accdc3e72848911b0cb25cfab7d2478ad0c996302c95744"
inject_controller_failures="${SDO_SMOKE_INJECT_CONTROLLER_FAILURES:-0}"
real_lifecycle="${SDO_SMOKE_REAL_LIFECYCLE:-0}"
runtime_pid=""
main_bash_pid="${BASHPID}"

cleanup() {
	local status="$?"
	# Command substitutions used by the recovery probes run in subshells. Never
	# let an inherited EXIT trap tear down the cluster while the parent smoke is
	# still waiting for the production receipt.
	if [[ "${BASHPID}" != "${main_bash_pid}" ]]; then
		return "${status}"
	fi
	if [[ "${status}" != "0" ]]; then
		kubectl --namespace "${namespace}" get pods,jobs,configmaps,leases 2>/dev/null || true
		kubectl --namespace "${namespace}" get configmap sdo-controller-state \
			--output jsonpath='{.data.runtime-state\.json}' 2>/dev/null || true
		kubectl --namespace "${namespace}" logs job/sdo-controller-run --all-containers=true \
			--tail=200 2>/dev/null || true
	fi
  if [[ -n "${runtime_pid}" ]]; then
    kill "${runtime_pid}" >/dev/null 2>&1 || true
  fi
  rm -rf "${workspace}"
  kind delete cluster --name "${cluster_name}" >/dev/null 2>&1 || true
	return "${status}"
}
trap cleanup EXIT

cat >"${kind_config}" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: ${cluster_name}
networking:
  disableDefaultCNI: true
  podSubnet: 192.168.0.0/16
nodes:
  - role: control-plane
EOF
kind create cluster --config "${kind_config}"
curl --fail --location --silent --show-error --retry 3 "${calico_url}" --output "${calico_manifest}"
echo "${calico_sha256}  ${calico_manifest}" | shasum -a 256 --check
kubectl create -f "${calico_manifest}"
kubectl --namespace kube-system rollout status daemonset/calico-node --timeout=300s
kubectl --namespace kube-system rollout status deployment/calico-kube-controllers --timeout=300s
kind load docker-image \
  sdo-controller:v0.1.0 \
  sdo-responder:v0.1.0 \
  sdo-detector-validator:v0.1.0 \
  --name "${cluster_name}"
kubectl create namespace "${namespace}"

mkdir -p "${application}/deploy"
cat >"${application}/deploy/application.yaml" <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: smoke-api
spec:
  replicas: 1
  selector:
    matchLabels: {app: smoke-api}
  template:
    metadata:
      labels: {app: smoke-api}
    spec:
      containers:
        - name: api
          image: nginx:1.29.0-alpine
          envFrom:
            - configMapRef:
                name: smoke-api-config
---
apiVersion: v1
kind: Service
metadata:
  name: smoke-api
spec:
  selector: {app: smoke-api}
  ports:
    - {port: 80, targetPort: 80}
EOF

git -C "${application}" init -q
git -C "${application}" config user.name "SDO Runtime Smoke"
git -C "${application}" config user.email "sdo-runtime-smoke@invalid"
git -C "${application}" add deploy/application.yaml
git -C "${application}" commit -qm "deploy smoke application"

cd "${repo_root}"
APP_ROOT="${application}" REAL_LIFECYCLE="${real_lifecycle}" uv run python - <<'PY'
import os
from pathlib import Path

from sdo.agent_runtime.lifecycle import ensure_operational_memory, run_initial_lifecycle

root = Path(os.environ["APP_ROOT"])
objective = "The smoke-api Deployment is available and its Service has a ready endpoint."
if os.environ["REAL_LIFECYCLE"] == "1":
    run_initial_lifecycle(
        root,
        application="sdo-runtime-smoke",
        health_objective=objective,
    )
else:
    # The crash-recovery smoke isolates the production controller/broker
    # protocol from model availability. Real fresh-session lifecycle agents
    # have their own opt-in integration; this is the explicit test double.
    ensure_operational_memory(
        root,
        application="sdo-runtime-smoke",
        health_objective=objective,
    )
PY

cat >"${application}/.sdo/diagnostics/detectors/health/objective/sandbox_isolation_test.go" <<'EOF'
package objective

import (
	"net"
	"os"
	"testing"
	"time"
)

func TestValidatorSandboxIsolation(t *testing.T) {
	for _, forbidden := range []string{
		"/var/run/secrets/kubernetes.io/serviceaccount/token",
		"/sdo/credentials/auth.json",
		"/workspace/application",
	} {
		if _, err := os.Stat(forbidden); !os.IsNotExist(err) {
			t.Fatalf("validator can access forbidden path %s: %v", forbidden, err)
		}
	}
	host := os.Getenv("KUBERNETES_SERVICE_HOST")
	port := os.Getenv("KUBERNETES_SERVICE_PORT")
	if host == "" || port == "" {
		t.Fatal("validator has no Kubernetes API address for the network-denial probe")
	}
	connection, err := net.DialTimeout("tcp", net.JoinHostPort(host, port), 750*time.Millisecond)
	if err == nil {
		connection.Close()
		t.Fatal("validator can reach the Kubernetes API despite the default-deny egress policy")
	}
}
EOF
git -C "${application}" add .sdo/diagnostics/detectors/health/objective/sandbox_isolation_test.go
git -C "${application}" commit -qm "test: add validator sandbox adversary"

current_controller_pod() {
  kubectl --namespace "${namespace}" get pods \
    --selector job-name=sdo-controller-run \
    --field-selector status.phase=Running \
    --output jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

current_lease_holder() {
  kubectl --namespace "${namespace}" get lease sdo-controller \
    --output jsonpath='{.spec.holderIdentity}' 2>/dev/null || true
}

wait_for_new_controller() {
  local previous="$1"
  local deadline=$((SECONDS + 120))
  local candidate=""
  while (( SECONDS < deadline )); do
    candidate="$(current_controller_pod)"
    if [[ -n "${candidate}" && "${candidate}" != "${previous}" ]]; then
      kubectl --namespace "${namespace}" wait --for=condition=Ready "pod/${candidate}" --timeout=90s >/dev/null
      echo "${candidate}"
      return 0
    fi
    sleep 1
  done
  echo "controller Job did not replace ${previous}" >&2
  return 1
}

wait_for_lease_handoff() {
  local previous="$1"
  local deadline=$((SECONDS + 120))
  local holder=""
  while (( SECONDS < deadline )); do
    holder="$(current_lease_holder)"
    if [[ -n "${holder}" && "${holder}" != "${previous}" ]]; then
      echo "${holder}"
      return 0
    fi
    sleep 1
  done
  echo "leader Lease did not hand off from ${previous}" >&2
  return 1
}

wait_for_dispatch_running() {
  local deadline=$((SECONDS + 300))
  local state=""
  while (( SECONDS < deadline )); do
    state="$(kubectl --namespace "${namespace}" get configmap sdo-controller-state \
      --output jsonpath='{.data.runtime-state\.json}' 2>/dev/null || true)"
    if [[ -n "${state}" ]] && jq -e \
      '(.dispatch_state == "running" or .dispatch_state == "pending") and .incident_open == true' \
      >/dev/null <<<"${state}" && \
      kubectl --namespace "${namespace}" get jobs \
        --selector app.kubernetes.io/name=sdo-responder \
        --output json 2>/dev/null | jq -e '.items | length == 1' >/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "controller never reached running dispatch state" >&2
  return 1
}

wait_for_pending_closure() {
  local deadline=$((SECONDS + 300))
  local state=""
  while (( SECONDS < deadline )); do
    state="$(kubectl --namespace "${namespace}" get configmap sdo-controller-state \
      --output jsonpath='{.data.runtime-state\.json}' 2>/dev/null || true)"
    if [[ -n "${state}" ]] && jq -e \
      '.pending_closure != null and .closure_state == "pending"' >/dev/null <<<"${state}"; then
      return 0
    fi
    sleep 1
  done
  echo "controller never persisted pending closure" >&2
  return 1
}

kubectl --namespace "${namespace}" apply -f "${application}/deploy/application.yaml"
if kubectl --namespace "${namespace}" wait --for=condition=available deployment/smoke-api --timeout=15s; then
  echo "fault did not fire: smoke-api unexpectedly became available" >&2
  exit 1
fi

APP_ROOT="${application}" \
NAMESPACE="${namespace}" \
MODEL="${SDO_SMOKE_MODEL:-gpt-5.4}" \
REAL_LIFECYCLE="${real_lifecycle}" \
uv run python - <<'PY' &
import os
from pathlib import Path

from benchmarks.sregym.adapter.runtime import RuntimeConfig, run_production_runtime

run_production_runtime(
    RuntimeConfig(
        repository=Path(os.environ["APP_ROOT"]),
        namespace=os.environ["NAMESPACE"],
        application="sdo-runtime-smoke",
        controller_image="sdo-controller:v0.1.0",
        responder_image="sdo-responder:v0.1.0",
        repository_pvc="sdo-application-repository",
        credentials_secret="sdo-codex-credentials",
        model=os.environ["MODEL"],
        timeout_seconds=1800,
        allow_test_lifecycle=os.environ["REAL_LIFECYCLE"] != "1",
    )
)
PY

runtime_pid=$!

if [[ "${inject_controller_failures}" == "1" ]]; then
  wait_for_dispatch_running
  first_controller_pod="$(current_controller_pod)"
  first_lease_holder="$(current_lease_holder)"
  test -n "${first_controller_pod}"
  test -n "${first_lease_holder}"
  kubectl --namespace "${namespace}" delete pod "${first_controller_pod}" \
    --grace-period=0 --force --wait=false
  second_controller_pod="$(wait_for_new_controller "${first_controller_pod}")"
  second_lease_holder="$(wait_for_lease_handoff "${first_lease_holder}")"
  test "${second_lease_holder}" != "${first_lease_holder}"

  wait_for_pending_closure
  responder_job_count="$(kubectl --namespace "${namespace}" get jobs \
    --selector app.kubernetes.io/name=sdo-responder \
    --output json | jq '.items | length')"
  test "${responder_job_count}" = "1"
  kubectl --namespace "${namespace}" delete pod "${second_controller_pod}" \
    --grace-period=0 --force --wait=false
  third_controller_pod="$(wait_for_new_controller "${second_controller_pod}")"
  test -n "$(wait_for_lease_handoff "${second_lease_holder}")"
  test "${third_controller_pod}" != "${second_controller_pod}"
fi

wait "${runtime_pid}"
runtime_pid=""

kubectl --namespace "${namespace}" wait --for=condition=available deployment/smoke-api --timeout=180s
test -s "${application}/.sdo/outcomes.jsonl"
commit_messages="$(git -C "${application}" log --format=%B)"
grep -q "SDO-Phase: proposal" <<<"${commit_messages}"
grep -q "SDO-Phase: outcome" <<<"${commit_messages}"
grep -q "SDO-Phase: reflection" <<<"${commit_messages}"
if [[ "${inject_controller_failures}" == "1" ]]; then
  test "$(grep -c "SDO-Phase: proposal" <<<"${commit_messages}")" = "1"
  test "$(grep -c "SDO-Phase: outcome" <<<"${commit_messages}")" = "1"
  test "$(grep -c "SDO-Phase: reflection" <<<"${commit_messages}")" = "1"
  test "$(wc -l <"${application}/.sdo/outcomes.jsonl" | tr -d ' ')" = "1"
  test "$(git -C "${application}" worktree list --porcelain | grep -c '^worktree ')" = "1"
  ledger_files=("${application}"/.git/sdo-broker/*.json)
  test "${#ledger_files[@]}" = "1"
  jq -e '.acknowledged == true and .cleaned == true' "${ledger_files[0]}" >/dev/null
fi
echo "production SDO runtime Kind smoke passed"
