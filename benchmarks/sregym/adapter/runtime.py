"""SREGym transport and receipt adapter for the production Kubernetes runtime."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from benchmarks.sregym.protocol import ProductionReceiptValidationError
from benchmarks.sregym.protocol import (
    validate_production_receipt as validate_receipt_contract,
)
from sdo.controller_install import (
    ControllerInstallConfig,
    ControllerInstallError,
    ControllerInstallResult,
    controller_security_contexts,
    install_controller,
    kubectl,
)
from sdo.controller_install import (
    controller_resources as production_controller_resources,
)
from sdo.operational_memory import ControllerRolloutRecord

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _string_object_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    mapping = cast("dict[object, object]", value)
    if not all(isinstance(key, str) for key in mapping):
        return None
    return cast("dict[str, object]", mapping)


def _json_object(raw: str, *, context: str) -> dict[str, object]:
    decoded: object = json.loads(raw)
    document = _string_object_mapping(decoded)
    if document is None:
        raise ControllerInstallError(f"{context} is not a JSON object")
    return document


def _object_list(value: object) -> list[object] | None:
    return cast("list[object]", value) if isinstance(value, list) else None


@dataclass(frozen=True)
class RuntimeConfig(ControllerInstallConfig):
    """Kubernetes runtime configuration extended with benchmark transport."""

    submission_api_base: str | None = None
    submission_relay_target_base: str | None = None
    allow_test_lifecycle: bool = False
    wait_for_completion: bool = True


def validate_production_receipt(receipt: dict[str, Any], *, allow_test_lifecycle: bool = False) -> None:
    try:
        validate_receipt_contract(receipt, allow_test_lifecycle=allow_test_lifecycle)
    except ProductionReceiptValidationError as exc:
        raise ControllerInstallError(str(exc)) from exc


@dataclass(frozen=True)
class _SREGymRuntimeExtension:
    config: RuntimeConfig

    def controller_args(self, config: ControllerInstallConfig) -> list[str]:
        args = ["--duration", f"{config.timeout_seconds}s", "--exit-after-closure"]
        if self.config.submission_api_base:
            args.extend(
                [
                    f"--responder-env=SDO_SREGYM_API_BASE={self.config.submission_api_base}",
                    f"--responder-env=SDO_RESPONDER_EXTRA_INSTRUCTIONS={_responder_instructions()}",
                ]
            )
        return args

    def resources(
        self,
        config: ControllerInstallConfig,
        pod_security: dict[str, Any],
        container_security: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return _submission_bridge_resources(self.config, pod_security, container_security)

    def wait_until_ready(self, config: ControllerInstallConfig) -> None:
        _wait_for_submission_bridge(self.config)

    def complete(self, config: ControllerInstallConfig, result: ControllerInstallResult) -> dict[str, Any]:
        receipt = _production_receipt(self.config, result.controller_logs)
        validate_production_receipt(receipt, allow_test_lifecycle=self.config.allow_test_lifecycle)
        print("SDO_PRODUCTION_RECEIPT=" + json.dumps(receipt, sort_keys=True))
        return receipt

    def cleanup(self, config: ControllerInstallConfig) -> None:
        _delete_submission_bridge(self.config)


def runtime_resources(config: RuntimeConfig) -> list[dict[str, Any]]:
    extension = _SREGymRuntimeExtension(config)
    pod_security, container_security = controller_security_contexts()
    return production_controller_resources(
        config,
        extra_controller_args=extension.controller_args(config),
        additional_resources=extension.resources(config, pod_security, container_security),
    )


def run_production_runtime(config: RuntimeConfig) -> dict[str, Any]:
    result = install_controller(config, _SREGymRuntimeExtension(config))
    return result


def _responder_instructions() -> str:
    return (
        "SREGym submission transport is available. After evidence-based diagnosis, run "
        "`python3 -m benchmarks.sregym.adapter.submission diagnosis '<diagnosis>'`; after repair and your own "
        "verification, run `python3 -m benchmarks.sregym.adapter.submission mitigation '<mitigation>'`. The latter "
        "also sends the autonomous done signal and waits for deferred diagnosis grading. Treat every response as "
        "benchmark transport or grading, never as health evidence: the controller alone determines closure from "
        "live detectors. Do not begin repair until diagnosis is acknowledged. Before mitigation, wait for every "
        "affected rollout to finish, require desired, updated, ready, and available replicas to agree, require each "
        "affected Service to expose a ready endpoint for the current rollout, and exercise a representative request. "
        "Do not return the incident result until mitigation reports done."
    )


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
                                "image": config.responder_image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["python3", "-m", "benchmarks.sregym.adapter.submission_relay"],
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
        completed = kubectl(
            ["get", "deployment/sdo-sregym-bridge", "-o", "json"],
            namespace=config.namespace,
            check=False,
        )
        if completed.returncode == 0:
            document = _json_object(completed.stdout, context="SREGym submission bridge deployment")
            status = _string_object_mapping(document.get("status"))
            if status is not None and status.get("readyReplicas") == 1:
                return
            last_details = completed.stdout
        else:
            last_details = completed.stderr or completed.stdout
        time.sleep(1)
    raise ControllerInstallError(f"SREGym submission bridge did not become ready: {last_details.strip()}")


def _delete_submission_bridge(config: RuntimeConfig) -> None:
    if config.submission_relay_target_base is None:
        return
    kubectl(
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
        raise ControllerInstallError("cannot locate Git common directory for production receipt")
    common_dir = Path(common_dir_result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = config.repository / common_dir
    jobs_payload = kubectl(
        ["get", "jobs", "--selector", "app.kubernetes.io/name=sdo-responder", "-o", "json"],
        namespace=config.namespace,
    )
    jobs_document = _json_object(jobs_payload.stdout, context="responder Job inventory")
    jobs = _object_list(jobs_document.get("items")) or []
    state_document = _json_object(
        kubectl(["get", "configmap/sdo-controller-state", "-o", "json"], namespace=config.namespace).stdout,
        context="controller state ConfigMap",
    )
    state_data = _string_object_mapping(state_document.get("data"))
    state_raw = state_data.get("runtime-state.json") if state_data is not None else None
    if not isinstance(state_raw, str):
        raise ControllerInstallError("controller state ConfigMap has no runtime-state.json data")
    state = _json_object(state_raw, context="controller runtime state")
    incident_id = state.get("last_acknowledged_incident_id")
    if not isinstance(incident_id, str) or not incident_id.strip():
        raise ControllerInstallError("controller has no durable acknowledged incident for receipt correlation")
    configmaps_payload = kubectl(["get", "configmaps", "-o", "json"], namespace=config.namespace)
    configmaps_document = _json_object(configmaps_payload.stdout, context="ConfigMap inventory")
    configmaps = _object_list(configmaps_document.get("items")) or []
    responder_jobs, result, responder_job_evidence = _resolve_responder_dispatch(
        incident_id=incident_id,
        jobs=jobs,
        configmaps=configmaps,
    )
    ledger = _load_incident_ledger(common_dir.resolve() / "sdo-broker", incident_id)
    remaining = kubectl(
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
    closure = _string_object_mapping(ledger.get("closure")) or {}
    detector_clear = closure.get("final_detector_states", [])
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
        raise ControllerInstallError("accepted detector update was not rolled out by the controller supervisor")
    return receipt


def _resolve_responder_dispatch(
    *,
    incident_id: str,
    jobs: Sequence[object],
    configmaps: Sequence[object],
) -> tuple[list[str], dict[str, object], str]:
    """Resolve one responder execution from its durable request/result protocol.

    Responder Jobs are intentionally finite and Kubernetes can garbage-collect
    them while a stopped cluster is offline.  The request and result ConfigMaps
    are the durable, idempotent protocol records used by the dispatcher itself;
    correlating both payloads to the controller's acknowledged incident keeps
    exact-once evidence recoverable without recreating or rerunning a responder.
    """

    documents: dict[str, dict[str, object]] = {}
    for raw_document in configmaps:
        document = _string_object_mapping(raw_document)
        if document is None:
            continue
        metadata = _string_object_mapping(document.get("metadata"))
        name = metadata.get("name") if metadata is not None else None
        if isinstance(name, str) and name:
            documents[name] = document

    pairs: list[tuple[str, dict[str, object]]] = []
    for request_name, request_document in documents.items():
        if not request_name.endswith("-request"):
            continue
        try:
            request_data = _string_object_mapping(request_document.get("data"))
            request_raw = request_data.get("incident-request.json") if request_data is not None else None
            if not isinstance(request_raw, str):
                continue
            request = _json_object(request_raw, context="responder request")
        except (TypeError, json.JSONDecodeError, ControllerInstallError):
            continue
        if request.get("incident_id") != incident_id:
            continue
        job_name = request_name.removesuffix("-request")
        result_document = documents.get(f"{job_name}-result")
        if result_document is None:
            continue
        try:
            result_data = _string_object_mapping(result_document.get("data"))
            result_raw = result_data.get("incident-result.json") if result_data is not None else None
            if not isinstance(result_raw, str):
                continue
            result = _json_object(result_raw, context="responder result")
        except (TypeError, json.JSONDecodeError, ControllerInstallError):
            continue
        if result.get("incident_id") == incident_id:
            pairs.append((job_name, result))

    if len(pairs) != 1:
        raise ControllerInstallError(
            "acknowledged incident requires exactly one durable responder "
            f"request/result ConfigMap pair; found {len(pairs)}"
        )
    job_name, result = pairs[0]
    live_jobs = sorted(
        str(metadata.get("name"))
        for job in jobs
        if (job_document := _string_object_mapping(job)) is not None
        if (metadata := _string_object_mapping(job_document.get("metadata"))) is not None
        if metadata.get("name")
    )
    if live_jobs and live_jobs != [job_name]:
        raise ControllerInstallError(
            f"live responder Jobs do not match durable incident execution: expected {[job_name]!r}, found {live_jobs!r}"
        )
    evidence = "live-job-and-durable-configmaps" if live_jobs else "durable-request-result-configmaps"
    return [job_name], result, evidence


def _load_incident_ledger(state_root: Path, incident_id: str) -> dict[str, object]:
    """Load exactly one ledger by embedded incident ID, independent of mtime."""

    matches: list[dict[str, object]] = []
    for path in state_root.glob("*.json"):
        try:
            decoded: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ControllerInstallError(f"cannot read durable broker ledger {path.name}: {exc}") from exc
        payload = _string_object_mapping(decoded)
        if payload is not None and payload.get("incident_id") == incident_id:
            matches.append(payload)
    if not matches:
        raise ControllerInstallError(f"production runtime produced no durable broker ledger for incident {incident_id}")
    if len(matches) != 1:
        raise ControllerInstallError(
            f"production runtime produced multiple durable broker ledgers for incident {incident_id}"
        )
    return matches[0]


def _validated_controller_rollout_record(ledger: Mapping[str, object]) -> dict[str, Any] | None:
    """Resolve the single authoritative successful attempt for this incident."""

    if ledger.get("controller_update_required") is not True:
        return None
    raw_records = ledger.get("controller_update_rollouts")
    records = _object_list(raw_records)
    if not records:
        raise ControllerInstallError("controller rollout ledger is missing durable attempt records")
    incident_id = ledger.get("incident_id")
    reflection_commit = ledger.get("reflection_commit")
    before = ledger.get("controller_update_before_fingerprint")
    after = ledger.get("controller_update_after_fingerprint")
    if not all(isinstance(value, str) and value for value in (incident_id, reflection_commit, before, after)):
        raise ControllerInstallError("controller rollout ledger is missing its expected detector transition")
    successful: list[ControllerRolloutRecord] = []
    for raw_record in records:
        try:
            record = ControllerRolloutRecord.model_validate(raw_record)
        except ValidationError as exc:
            raise ControllerInstallError(f"invalid durable controller rollout record: {exc}") from exc
        if record.incident_id != incident_id:
            raise ControllerInstallError("durable controller rollout incident ID mismatch")
        if record.reflection_commit != reflection_commit:
            raise ControllerInstallError("durable controller rollout reflection commit mismatch")
        if record.before_detector_fingerprint != before or record.after_detector_fingerprint != after:
            raise ControllerInstallError("durable controller rollout detector transition mismatch")
        if record.success:
            successful.append(record)
    if not successful:
        returncodes = [
            record.get("returncode")
            for raw_record in records
            if (record := _string_object_mapping(raw_record)) is not None
        ]
        raise ControllerInstallError(f"durable controller rollout has no returncode=0 attempt: {returncodes}")
    if len(successful) != 1:
        raise ControllerInstallError("durable controller rollout requires exactly one successful attempt")
    return successful[0].model_dump(mode="json")
