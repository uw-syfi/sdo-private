"""Install and run the production SDO controller in the benchmark namespace."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app_operator.memory.broker_service import ControllerRolloutRecord
from libs.sregym_lib.production_receipt import (
    ProductionReceiptValidationError,
)
from libs.sregym_lib.production_receipt import (
    validate_production_receipt as validate_receipt_contract,
)


class RuntimeInstallError(RuntimeError):
    """Raised when the in-cluster SDO runtime cannot be installed or completed."""


CODEX_HOME_PATH = "/workspace/.sdo-runtime/codex"
RUNTIME_BUILD_ROOT = "/workspace/.sdo-runtime/build"
RUNTIME_TMPDIR = f"{RUNTIME_BUILD_ROOT}/tmp"
RUNTIME_GO_TMPDIR = f"{RUNTIME_BUILD_ROOT}/go-tmp"
RUNTIME_GO_CACHE = f"{RUNTIME_BUILD_ROOT}/go-cache"


def validate_production_receipt(receipt: dict[str, Any], *, allow_test_lifecycle: bool = False) -> None:
    """Preserve the runtime API while sharing one cross-layer contract."""

    try:
        validate_receipt_contract(receipt, allow_test_lifecycle=allow_test_lifecycle)
    except ProductionReceiptValidationError as exc:
        raise RuntimeInstallError(str(exc)) from exc


@dataclass(frozen=True)
class RuntimeConfig:
    repository: Path
    namespace: str
    application: str
    controller_image: str
    responder_image: str
    repository_pvc: str
    credentials_secret: str
    model: str
    timeout_seconds: int
    validator_image: str = "sdo-observer-validator:v0.1.0"
    submission_api_base: str | None = None
    submission_relay_target_base: str | None = None
    allow_test_lifecycle: bool = False


def runtime_resources(config: RuntimeConfig) -> list[dict[str, Any]]:
    controller_args = [
        "controller",
        "--app",
        "/workspace/application",
        "--namespace",
        config.namespace,
        "--application",
        config.application,
        "--responder-image",
        config.responder_image,
        "--repository-pvc",
        config.repository_pvc,
        "--repository-mount-path",
        "/workspace",
        "--credentials-secret",
        config.credentials_secret,
        "--worktree-root",
        "/workspace/worktrees",
        "--duration",
        f"{config.timeout_seconds}s",
        "--exit-after-closure",
        f"--responder-env=CODEX_HOME={CODEX_HOME_PATH}",
        "--broker-arg=-m",
        "--broker-arg=app_operator.responder.broker_cli",
        "--broker-arg=--proposal-command",
        "--broker-arg=git diff --check HEAD --",
        "--broker-arg=--validator-mode",
        "--broker-arg=kubernetes",
        "--broker-arg=--validator-namespace",
        f"--broker-arg={config.namespace}",
        "--broker-arg=--validator-image",
        f"--broker-arg={config.validator_image}",
        "--broker-arg=--validator-repository-pvc",
        f"--broker-arg={config.repository_pvc}",
        "--broker-arg=--validator-repository-mount-path",
        "--broker-arg=/workspace",
        "--broker-arg=--responder-model",
        f"--broker-arg={config.model}",
        "--broker-arg=--reflection-model",
        f"--broker-arg={config.model}",
    ]
    if config.submission_api_base:
        controller_args.extend(
            [
                f"--responder-env=SDO_SREGYM_API_BASE={config.submission_api_base}",
                "--responder-env=SDO_SREGYM_SUBMISSION_BRIDGE=1",
            ]
        )
    pod_security = {
        "runAsNonRoot": True,
        "runAsUser": 65532,
        "fsGroup": 65532,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    container_security = {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    return [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": config.repository_pvc, "namespace": config.namespace},
            "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "10Gi"}}},
        },
        *_rbac_resources(config.namespace),
        *_submission_bridge_resources(config, pod_security, container_security),
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "sdo-repository-sync", "namespace": config.namespace},
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "securityContext": pod_security,
                "containers": [
                    {
                        "name": "sync",
                        "image": config.controller_image,
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["sleep", "3600"],
                        "securityContext": container_security,
                        "resources": {
                            "requests": {"cpu": "50m", "memory": "64Mi"},
                            "limits": {"cpu": "250m", "memory": "256Mi"},
                        },
                        "volumeMounts": [
                            {"name": "repository", "mountPath": "/workspace"},
                            {"name": "scratch", "mountPath": "/tmp"},
                        ],
                    }
                ],
                "volumes": [
                    {"name": "repository", "persistentVolumeClaim": {"claimName": config.repository_pvc}},
                    {"name": "scratch", "emptyDir": {}},
                ],
            },
        },
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "sdo-controller-run", "namespace": config.namespace},
            "spec": {
                "backoffLimit": 6,
                "podFailurePolicy": {
                    "rules": [
                        {
                            "action": "Ignore",
                            "onPodConditions": [{"type": "DisruptionTarget", "status": "True"}],
                        }
                    ]
                },
                "ttlSecondsAfterFinished": 600,
                "template": {
                    "metadata": {"labels": {"app.kubernetes.io/name": "sdo-controller"}},
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": "sdo-controller",
                        "securityContext": pod_security,
                        "initContainers": [
                            {
                                "name": "prepare-build-cache",
                                "image": config.controller_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": [
                                    "mkdir",
                                    "-p",
                                    RUNTIME_TMPDIR,
                                    RUNTIME_GO_TMPDIR,
                                    RUNTIME_GO_CACHE,
                                ],
                                "securityContext": container_security,
                                "resources": {
                                    "requests": {"cpu": "10m", "memory": "16Mi"},
                                    "limits": {"cpu": "100m", "memory": "64Mi"},
                                },
                                "volumeMounts": [{"name": "repository", "mountPath": "/workspace"}],
                            }
                        ],
                        "containers": [
                            {
                                "name": "controller",
                                "image": config.controller_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["python3", "-m", "observer.updater.check_cli"],
                                "args": controller_args,
                                "envFrom": [{"secretRef": {"name": config.credentials_secret}}],
                                "env": [
                                    {"name": "CODEX_HOME", "value": CODEX_HOME_PATH},
                                    {"name": "TMPDIR", "value": RUNTIME_TMPDIR},
                                    {"name": "GOTMPDIR", "value": RUNTIME_GO_TMPDIR},
                                    {"name": "GOCACHE", "value": RUNTIME_GO_CACHE},
                                    {"name": "SDO_CONTROLLER_JOB", "value": "sdo-controller-run"},
                                    {
                                        "name": "SDO_CONTROLLER_POD_UID",
                                        "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}},
                                    },
                                    {"name": "GIT_AUTHOR_NAME", "value": "sdo-controller"},
                                    {"name": "GIT_AUTHOR_EMAIL", "value": "sdo-controller@invalid"},
                                    {"name": "GIT_COMMITTER_NAME", "value": "sdo-controller"},
                                    {"name": "GIT_COMMITTER_EMAIL", "value": "sdo-controller@invalid"},
                                ],
                                "securityContext": container_security,
                                "resources": {
                                    "requests": {"cpu": "250m", "memory": "512Mi"},
                                    "limits": {"cpu": "2", "memory": "4Gi"},
                                },
                                "volumeMounts": [
                                    {"name": "repository", "mountPath": "/workspace"},
                                    {"name": "scratch", "mountPath": "/tmp"},
                                    {"name": "credentials", "mountPath": "/sdo/credentials", "readOnly": True},
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "repository", "persistentVolumeClaim": {"claimName": config.repository_pvc}},
                            {"name": "scratch", "emptyDir": {"sizeLimit": "4Gi"}},
                            {"name": "credentials", "secret": {"secretName": config.credentials_secret}},
                        ],
                    },
                },
            },
        },
    ]


def run_production_runtime(config: RuntimeConfig) -> dict[str, Any]:
    resources = runtime_resources(config)
    base = resources[:-1]
    controller_job = resources[-1]
    _kubectl(["apply", "-f", "-"], namespace=config.namespace, input_text=yaml.safe_dump_all(base))
    _ensure_credentials_secret(config)
    try:
        _wait_for_submission_bridge(config)
        _wait_for_repository_sync(config.namespace)
        _kubectl(
            [
                "exec",
                "sdo-repository-sync",
                "--",
                "sh",
                "-c",
                "rm -rf /workspace/application /workspace/worktrees "
                "&& mkdir -p /workspace/application /workspace/worktrees",
            ],
            namespace=config.namespace,
        )
        _copy_repository_to_pod(config)
        _kubectl(["delete", "job/sdo-controller-run", "--ignore-not-found=true"], namespace=config.namespace)
        _kubectl(["apply", "-f", "-"], namespace=config.namespace, input_text=yaml.safe_dump(controller_job))
        try:
            _wait_for_controller_job(config)
        except RuntimeInstallError:
            _kubectl(["logs", "job/sdo-controller-run"], namespace=config.namespace, check=False)
            raise
        controller_logs = _controller_job_logs(config.namespace)
        _copy_repository_from_pod(config)
        receipt = _production_receipt(config, controller_logs)
        validate_production_receipt(receipt, allow_test_lifecycle=config.allow_test_lifecycle)
        print("SDO_PRODUCTION_RECEIPT=" + json.dumps(receipt, sort_keys=True))
        return receipt
    finally:
        _delete_repository_sync(config.namespace)
        _delete_submission_bridge(config)


def _submission_bridge_resources(
    config: RuntimeConfig,
    pod_security: dict[str, Any],
    container_security: dict[str, Any],
) -> list[dict[str, Any]]:
    if config.submission_relay_target_base is None:
        return []
    labels = {"app.kubernetes.io/name": "sdo-sregym-bridge"}
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "sdo-sregym-bridge", "namespace": config.namespace},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "hostNetwork": True,
                        "dnsPolicy": "ClusterFirstWithHostNet",
                        "automountServiceAccountToken": False,
                        "securityContext": pod_security,
                        "containers": [
                            {
                                "name": "relay",
                                "image": config.controller_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["python3", "-m", "app_operator.sdo_sregym.submission_relay"],
                                "args": [
                                    "--listen-port",
                                    "18000",
                                    "--target-base",
                                    config.submission_relay_target_base,
                                ],
                                "ports": [{"name": "http", "containerPort": 18000, "protocol": "TCP"}],
                                "readinessProbe": {
                                    "httpGet": {"path": "/healthz", "port": 18000},
                                    "periodSeconds": 2,
                                    "timeoutSeconds": 6,
                                    "failureThreshold": 30,
                                },
                                "securityContext": container_security,
                                "resources": {
                                    "requests": {"cpu": "10m", "memory": "32Mi"},
                                    "limits": {"cpu": "100m", "memory": "128Mi"},
                                },
                            }
                        ],
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "sdo-sregym-bridge", "namespace": config.namespace},
            "spec": {
                "selector": labels,
                "ports": [{"name": "http", "port": 8000, "targetPort": 18000}],
            },
        },
    ]


def _wait_for_submission_bridge(config: RuntimeConfig) -> None:
    if config.submission_relay_target_base is None:
        return
    deadline = time.monotonic() + 120
    last_details = "deployment not observed"
    while time.monotonic() < deadline:
        completed = _kubectl(
            ["get", "deployment/sdo-sregym-bridge", "-o", "json"],
            namespace=config.namespace,
            check=False,
        )
        if completed.returncode == 0:
            document = json.loads(completed.stdout)
            if document.get("status", {}).get("readyReplicas") == 1:
                return
            last_details = completed.stdout
        else:
            last_details = completed.stderr or completed.stdout
        time.sleep(1)
    raise RuntimeInstallError(f"SREGym submission bridge did not become ready: {last_details.strip()}")


def _delete_submission_bridge(config: RuntimeConfig) -> None:
    if config.submission_relay_target_base is None:
        return
    _kubectl(
        [
            "delete",
            "deployment/sdo-sregym-bridge",
            "service/sdo-sregym-bridge",
            "--ignore-not-found=true",
            "--wait=false",
        ],
        namespace=config.namespace,
        check=False,
    )


def _delete_repository_sync(namespace: str) -> None:
    """Request cleanup without waiting on watch behavior unsupported by the filtered proxy."""

    _kubectl(
        ["delete", "pod/sdo-repository-sync", "--ignore-not-found=true", "--wait=false"],
        namespace=namespace,
        check=False,
    )


def _controller_job_logs(namespace: str) -> str:
    """Read the successful retry pod so transient failed-pod logs cannot hide rollout evidence."""

    pods_result = _kubectl(
        ["get", "pods", "--selector", "job-name=sdo-controller-run", "-o", "json"],
        namespace=namespace,
        check=False,
    )
    if pods_result.returncode == 0:
        items = json.loads(pods_result.stdout).get("items", [])
        succeeded = sorted(
            str(item.get("metadata", {}).get("name", ""))
            for item in items
            if isinstance(item, dict) and item.get("status", {}).get("phase") == "Succeeded"
        )
        if succeeded:
            return _kubectl(["logs", f"pod/{succeeded[-1]}"], namespace=namespace, check=False).stdout
    return _kubectl(["logs", "job/sdo-controller-run"], namespace=namespace, check=False).stdout


def _production_receipt(config: RuntimeConfig, controller_logs: str) -> dict[str, Any]:
    # Logs remain useful diagnostics, but are intentionally not authoritative:
    # Kubernetes may garbage-collect a failed or even successful retry Pod.
    del controller_logs
    common_dir_result = subprocess.run(
        ["git", "-C", str(config.repository), "rev-parse", "--git-common-dir"],
        check=False,
        capture_output=True,
        text=True,
    )
    if common_dir_result.returncode != 0:
        raise RuntimeInstallError("cannot locate Git common directory for production receipt")
    common_dir = Path(common_dir_result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = config.repository / common_dir
    jobs_payload = _kubectl(
        ["get", "jobs", "--selector", "app.kubernetes.io/name=sdo-responder", "-o", "json"],
        namespace=config.namespace,
    )
    jobs = json.loads(jobs_payload.stdout).get("items", [])
    state_document = json.loads(
        _kubectl(["get", "configmap/sdo-controller-state", "-o", "json"], namespace=config.namespace).stdout
    )
    state = json.loads(state_document.get("data", {}).get("runtime-state.json", "{}"))
    incident_id = state.get("last_acknowledged_incident_id")
    if not isinstance(incident_id, str) or not incident_id.strip():
        raise RuntimeInstallError("controller has no durable acknowledged incident for receipt correlation")
    configmaps_payload = _kubectl(["get", "configmaps", "-o", "json"], namespace=config.namespace)
    configmaps = json.loads(configmaps_payload.stdout).get("items", [])
    responder_jobs, result, responder_job_evidence = _resolve_responder_dispatch(
        incident_id=incident_id,
        jobs=jobs,
        configmaps=configmaps,
    )
    ledger = _load_incident_ledger(common_dir.resolve() / "sdo-broker", incident_id)
    remaining = _kubectl(
        [
            "exec",
            "sdo-repository-sync",
            "--",
            "find",
            "/workspace/worktrees",
            "-mindepth",
            "1",
            "-maxdepth",
            "1",
            "-print",
        ],
        namespace=config.namespace,
    )
    closure = ledger.get("closure") if isinstance(ledger.get("closure"), dict) else {}
    detector_clear = closure.get("final_detector_states", []) if isinstance(closure, dict) else []
    accepted_detector_paths = ledger.get("accepted_detector_paths", [])
    rollout_record = _validated_controller_rollout_record(ledger)
    controller_update_rollout = rollout_record is not None
    receipt: dict[str, Any] = {
        "schema_version": "sdo.production-receipt/v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "pre_cutover": False,
        "incident_id": incident_id,
        "namespace": config.namespace,
        "controller_workload": "batch/v1 Job/sdo-controller-run",
        "controller_image": config.controller_image,
        "responder_image": config.responder_image,
        "validator_image": config.validator_image,
        "validator_mode": "kubernetes-job",
        "production_job_dispatch": len(responder_jobs) == 1,
        "responder_jobs": responder_jobs,
        "responder_job_evidence": responder_job_evidence,
        "completed": result.get("status") == "completed",
        "proposal_commit": ledger.get("proposal_commit"),
        "outcome_commit": ledger.get("outcome_commit"),
        "reflection_commit": ledger.get("reflection_commit"),
        "validator_evidence_commit": ledger.get("validator_evidence_commit"),
        "responder_session_id": ledger.get("responder_session_id"),
        "same_session_reflection": bool(ledger.get("responder_session_id") and ledger.get("reflection_commit")),
        "detector_clear": detector_clear,
        "independent_verification": result.get("verification_evidence", []),
        "validator_network_policy_canaries": ledger.get("validator_network_policy_canaries", []),
        "acknowledged": ledger.get("acknowledged") is True,
        "cleaned": ledger.get("cleaned") is True,
        "remaining_worktrees": [line for line in remaining.stdout.splitlines() if line.strip()],
        "accepted_detector_paths": accepted_detector_paths,
        "controller_update_required": ledger.get("controller_update_required") is True,
        "controller_update_rollout": controller_update_rollout,
        "controller_update_rollout_record": rollout_record,
        "stale_memory_detected": ledger.get("stale_memory_detected") is True,
        "architecture_topology_fingerprint": ledger.get("architecture_topology_fingerprint"),
        "source_topology_fingerprint": ledger.get("source_topology_fingerprint"),
        "lifecycle_provenance": (config.repository / ".sdo" / "lifecycle-provenance.yaml").is_file(),
    }
    if receipt["controller_update_required"] and not controller_update_rollout:
        raise RuntimeInstallError("accepted detector update was not rolled out by the controller supervisor")
    return receipt


def _resolve_responder_dispatch(
    *,
    incident_id: str,
    jobs: list[Any],
    configmaps: list[Any],
) -> tuple[list[str], dict[str, Any], str]:
    """Resolve one responder execution from its durable request/result protocol.

    Responder Jobs are intentionally finite and Kubernetes can garbage-collect
    them while a stopped cluster is offline.  The request and result ConfigMaps
    are the durable, idempotent protocol records used by the dispatcher itself;
    correlating both payloads to the controller's acknowledged incident keeps
    exact-once evidence recoverable without recreating or rerunning a responder.
    """

    documents: dict[str, dict[str, Any]] = {}
    for raw_document in configmaps:
        if not isinstance(raw_document, dict):
            continue
        name = raw_document.get("metadata", {}).get("name")
        if isinstance(name, str) and name:
            documents[name] = raw_document

    pairs: list[tuple[str, dict[str, Any]]] = []
    for request_name, request_document in documents.items():
        if not request_name.endswith("-request"):
            continue
        try:
            request = json.loads(request_document.get("data", {}).get("incident-request.json", ""))
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(request, dict) or request.get("incident_id") != incident_id:
            continue
        job_name = request_name.removesuffix("-request")
        result_document = documents.get(f"{job_name}-result")
        if result_document is None:
            continue
        try:
            result = json.loads(result_document.get("data", {}).get("incident-result.json", ""))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(result, dict) and result.get("incident_id") == incident_id:
            pairs.append((job_name, result))

    if len(pairs) != 1:
        raise RuntimeInstallError(
            "acknowledged incident requires exactly one durable responder "
            f"request/result ConfigMap pair; found {len(pairs)}"
        )
    job_name, result = pairs[0]
    live_jobs = sorted(
        str(job.get("metadata", {}).get("name", ""))
        for job in jobs
        if isinstance(job, dict) and job.get("metadata", {}).get("name")
    )
    if live_jobs and live_jobs != [job_name]:
        raise RuntimeInstallError(
            f"live responder Jobs do not match durable incident execution: expected {[job_name]!r}, found {live_jobs!r}"
        )
    evidence = "live-job-and-durable-configmaps" if live_jobs else "durable-request-result-configmaps"
    return [job_name], result, evidence


def _load_incident_ledger(state_root: Path, incident_id: str) -> dict[str, Any]:
    """Load exactly one ledger by embedded incident ID, independent of mtime."""

    matches: list[dict[str, Any]] = []
    for path in state_root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeInstallError(f"cannot read durable broker ledger {path.name}: {exc}") from exc
        if isinstance(payload, dict) and payload.get("incident_id") == incident_id:
            matches.append(payload)
    if not matches:
        raise RuntimeInstallError(f"production runtime produced no durable broker ledger for incident {incident_id}")
    if len(matches) != 1:
        raise RuntimeInstallError(
            f"production runtime produced multiple durable broker ledgers for incident {incident_id}"
        )
    return matches[0]


def _validated_controller_rollout_record(ledger: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the single authoritative successful attempt for this incident."""

    if ledger.get("controller_update_required") is not True:
        return None
    raw_records = ledger.get("controller_update_rollouts")
    if not isinstance(raw_records, list) or not raw_records:
        raise RuntimeInstallError("controller rollout ledger is missing durable attempt records")
    incident_id = ledger.get("incident_id")
    reflection_commit = ledger.get("reflection_commit")
    before = ledger.get("controller_update_before_fingerprint")
    after = ledger.get("controller_update_after_fingerprint")
    if not all(isinstance(value, str) and value for value in (incident_id, reflection_commit, before, after)):
        raise RuntimeInstallError("controller rollout ledger is missing its expected detector transition")
    successful: list[ControllerRolloutRecord] = []
    for raw_record in raw_records:
        try:
            record = ControllerRolloutRecord.model_validate(raw_record)
        except ValidationError as exc:
            raise RuntimeInstallError(f"invalid durable controller rollout record: {exc}") from exc
        if record.incident_id != incident_id:
            raise RuntimeInstallError("durable controller rollout incident ID mismatch")
        if record.reflection_commit != reflection_commit:
            raise RuntimeInstallError("durable controller rollout reflection commit mismatch")
        if record.before_detector_fingerprint != before or record.after_detector_fingerprint != after:
            raise RuntimeInstallError("durable controller rollout detector transition mismatch")
        if record.success:
            successful.append(record)
    if not successful:
        returncodes = [record.get("returncode") for record in raw_records if isinstance(record, dict)]
        raise RuntimeInstallError(f"durable controller rollout has no returncode=0 attempt: {returncodes}")
    if len(successful) != 1:
        raise RuntimeInstallError("durable controller rollout requires exactly one successful attempt")
    return successful[0].model_dump(mode="json")


def _controller_update_rollout_succeeded(controller_logs: str) -> bool:
    for raw_line in controller_logs.splitlines():
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        fingerprint = payload.get("controller_update_rollout")
        returncode = payload.get("returncode")
        if isinstance(fingerprint, str) and fingerprint and type(returncode) is int and returncode == 0:
            return True
    return False


def _rbac_resources(namespace: str) -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    path = root / "observer" / "controller" / "deploy" / "rbac.yaml"
    resources = [document for document in yaml.safe_load_all(path.read_text(encoding="utf-8")) if document]
    for resource in resources:
        resource.setdefault("metadata", {})["namespace"] = namespace
    return resources


def _wait_for_controller_job(config: RuntimeConfig) -> None:
    deadline = time.monotonic() + config.timeout_seconds + 300
    while time.monotonic() < deadline:
        completed = _kubectl(
            ["get", "job/sdo-controller-run", "-o", "json"],
            namespace=config.namespace,
            check=False,
        )
        if completed.returncode != 0:
            time.sleep(2)
            continue
        state = _job_state(json.loads(completed.stdout))
        if state == "complete":
            return
        if state == "failed":
            raise RuntimeInstallError("controller Job failed")
        time.sleep(2)
    raise RuntimeInstallError("controller Job did not complete before its runtime deadline")


def _wait_for_repository_sync(namespace: str, timeout_seconds: int = 180) -> None:
    """Poll readiness because SREGym's filtered API proxy does not support kubectl watch reliably."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        completed = _kubectl(
            ["get", "pod/sdo-repository-sync", "-o", "json"],
            namespace=namespace,
            check=False,
        )
        if completed.returncode == 0 and _pod_is_ready(json.loads(completed.stdout)):
            return
        time.sleep(2)
    raise RuntimeInstallError("repository sync Pod did not become ready")


def _pod_is_ready(pod: dict[str, Any]) -> bool:
    status = pod.get("status")
    if not isinstance(status, dict):
        return False
    conditions = status.get("conditions", [])
    return isinstance(conditions, list) and any(
        isinstance(condition, dict) and condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in conditions
    )


def _job_state(job: dict[str, Any]) -> str:
    status = job.get("status")
    if not isinstance(status, dict):
        return "running"
    if status.get("succeeded", 0):
        return "complete"
    conditions = status.get("conditions", [])
    if isinstance(conditions, list) and any(
        isinstance(condition, dict) and condition.get("type") == "Failed" and condition.get("status") == "True"
        for condition in conditions
    ):
        return "failed"
    return "running"


def _ensure_credentials_secret(config: RuntimeConfig) -> None:
    completed = _kubectl(
        ["get", f"secret/{config.credentials_secret}"],
        namespace=config.namespace,
        check=False,
    )
    if completed.returncode == 0:
        return
    secret_data: dict[str, str] = {}
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        secret_data["OPENAI_API_KEY"] = api_key
    codex_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
    auth_file = codex_home / "auth.json"
    if auth_file.is_file():
        secret_data["auth.json"] = auth_file.read_text(encoding="utf-8")
    if not secret_data:
        raise RuntimeInstallError(
            f"Secret {config.credentials_secret!r} does not exist and no Codex credentials are available"
        )
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": config.credentials_secret, "namespace": config.namespace},
        "type": "Opaque",
        "stringData": secret_data,
    }
    _kubectl(["apply", "-f", "-"], namespace=config.namespace, input_text=yaml.safe_dump(secret))


def _copy_repository_to_pod(config: RuntimeConfig) -> None:
    producer = subprocess.Popen(
        ["tar", "-C", str(config.repository), "-cf", "-", "."],
        stdout=subprocess.PIPE,
        env=_tar_environment(),
    )
    if producer.stdout is None:
        raise RuntimeInstallError("failed to open repository tar stream")
    consumer = subprocess.run(
        [
            "kubectl",
            "--namespace",
            config.namespace,
            "exec",
            "-i",
            "sdo-repository-sync",
            "--",
            "tar",
            "-C",
            "/workspace/application",
            "-xf",
            "-",
        ],
        stdin=producer.stdout,
        check=False,
        capture_output=True,
        text=False,
        env=_tar_environment(),
    )
    producer.stdout.close()
    producer_returncode = producer.wait()
    if producer_returncode != 0 or consumer.returncode != 0:
        raise RuntimeInstallError("failed to seed application repository into the shared PVC")


def _copy_repository_from_pod(config: RuntimeConfig) -> None:
    producer = subprocess.Popen(
        [
            "kubectl",
            "--namespace",
            config.namespace,
            "exec",
            "sdo-repository-sync",
            "--",
            "tar",
            "-C",
            "/workspace/application",
            "-cf",
            "-",
            ".",
        ],
        stdout=subprocess.PIPE,
    )
    if producer.stdout is None:
        raise RuntimeInstallError("failed to open runtime repository tar stream")
    consumer = subprocess.run(
        ["tar", "-C", str(config.repository), "-xf", "-"],
        stdin=producer.stdout,
        check=False,
        capture_output=True,
        text=False,
        env=_tar_environment(),
    )
    producer.stdout.close()
    producer_returncode = producer.wait()
    if producer_returncode != 0 or consumer.returncode != 0:
        raise RuntimeInstallError("failed to synchronize accepted runtime commits back to the benchmark workspace")


def _tar_environment() -> dict[str, str]:
    return {**os.environ, "COPYFILE_DISABLE": "1"}


def _kubectl(
    args: list[str],
    *,
    namespace: str,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["kubectl", "--namespace", namespace, *args],
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeInstallError(f"kubectl {' '.join(args)} failed: {details}")
    return completed
