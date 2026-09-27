"""SREGym transport and receipt adapter for the production Kubernetes runtime."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    if not isinstance(result, dict):
        raise ControllerInstallError("benchmark runtime did not produce a production receipt")
    return result


def _responder_instructions() -> str:
    return (
        "SREGym submission transport is available. After evidence-based diagnosis, run "
        "`python3 -m benchmarks.sregym.adapter.submission diagnosis '<diagnosis>'`; after repair and your own "
        "verification, run `python3 -m benchmarks.sregym.adapter.submission mitigation '<mitigation>'`. The latter "
        "also sends the autonomous done signal and waits for benchmark grading. Treat every response as "
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
            document = json.loads(completed.stdout)
            if document.get("status", {}).get("readyReplicas") == 1:
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
    jobs = json.loads(jobs_payload.stdout).get("items", [])
    state_document = json.loads(
        kubectl(["get", "configmap/sdo-controller-state", "-o", "json"], namespace=config.namespace).stdout
    )
    state = json.loads(state_document.get("data", {}).get("runtime-state.json", "{}"))
    incident_id = state.get("last_acknowledged_incident_id")
    if not isinstance(incident_id, str) or not incident_id.strip():
        raise ControllerInstallError("controller has no durable acknowledged incident for receipt correlation")
    configmaps_payload = kubectl(["get", "configmaps", "-o", "json"], namespace=config.namespace)
    configmaps = json.loads(configmaps_payload.stdout).get("items", [])
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
    raw_closure = ledger.get("closure")
    closure: dict[str, Any] = raw_closure if isinstance(raw_closure, dict) else {}
    detector_clear = closure.get("final_detector_states", [])
    accepted_detector_paths = ledger.get("accepted_detector_paths", [])
    rollout_record = _validated_controller_rollout_record(ledger)
    controller_update_rollout = rollout_record is not None
    recorded_at = datetime.now(timezone.utc)
    receipt: dict[str, Any] = {
        "schema_version": "sdo.production-receipt/v1",
        "recorded_at": recorded_at.isoformat(),
        "pre_cutover": False,
        "incident_id": incident_id,
        "namespace": config.namespace,
        "controller_workload": "batch/v1 Job/sdo-controller-run",
        "controller_image": config.controller_image,
        "responder_image": config.responder_image,
        "validator_image": config.validator_image,
        "validator_mode": "kubernetes-job",
        "validator_execution_required": bool(accepted_detector_paths),
        "validator_skipped_reason": None if accepted_detector_paths else "unchanged-diagnostics",
        "production_job_dispatch": len(responder_jobs) == 1,
        "responder_jobs": responder_jobs,
        "responder_job_evidence": responder_job_evidence,
        "completed": result.get("status") == "completed",
        "confirmed_root_causes": result.get("confirmed_root_causes", []),
        "repair_policy": config.repair_policy,
        "repair_actions": result.get("repair_actions", []),
        "proposal_commit": ledger.get("proposal_commit"),
        "outcome_commit": ledger.get("outcome_commit"),
        "reflection_commit": ledger.get("reflection_commit"),
        "validator_evidence_commit": ledger.get("validator_evidence_commit"),
        "responder_session_id": ledger.get("responder_session_id"),
        "same_session_reflection": bool(ledger.get("responder_session_id") and ledger.get("reflection_commit")),
        "detector_clear": detector_clear,
        "independent_verification": result.get("verification_evidence", []),
        "usage": result.get("usage", {}),
        "reflection_usage": ledger.get("reflection_usage", {}),
        "phase_timings_seconds": _phase_timings(closure, recorded_at),
        "memory_reuse": _memory_reuse_summary(closure, result),
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


def _phase_timings(closure: dict[str, Any], recorded_at: datetime) -> dict[str, float]:
    def timestamp(name: str) -> datetime:
        value = closure.get(name)
        if not isinstance(value, str):
            raise ControllerInstallError(f"broker closure has no {name} timestamp")
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    detected = timestamp("detected_at")
    dispatched = timestamp("dispatched_at")
    responder_completed = timestamp("responder_completed_at")
    verified = timestamp("verified_at")
    return {
        "detection_to_dispatch": (dispatched - detected).total_seconds(),
        "responder": (responder_completed - dispatched).total_seconds(),
        "verification": (verified - responder_completed).total_seconds(),
        "operational_recovery": (verified - detected).total_seconds(),
        "post_recovery_learning_and_receipt": (recorded_at - verified).total_seconds(),
        "total": (recorded_at - detected).total_seconds(),
    }


def _memory_reuse_summary(closure: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    request = closure.get("request") if isinstance(closure.get("request"), dict) else {}
    relevant = request.get("relevant_outcomes", []) if isinstance(request, dict) else []
    relevant = relevant if isinstance(relevant, list) else []
    applied = result.get("applied_playbooks", [])
    applied = applied if isinstance(applied, list) else []
    return {
        "candidate_count": len(relevant),
        "match_reasons": sorted(
            {str(item.get("match_reason")) for item in relevant if isinstance(item, dict) and item.get("match_reason")}
        ),
        "applied_playbook_count": len(applied),
        "warm_path": bool(relevant),
    }


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
        raise ControllerInstallError(
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
        raise ControllerInstallError(
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
            raise ControllerInstallError(f"cannot read durable broker ledger {path.name}: {exc}") from exc
        if isinstance(payload, dict) and payload.get("incident_id") == incident_id:
            matches.append(payload)
    if not matches:
        raise ControllerInstallError(f"production runtime produced no durable broker ledger for incident {incident_id}")
    if len(matches) != 1:
        raise ControllerInstallError(
            f"production runtime produced multiple durable broker ledgers for incident {incident_id}"
        )
    return matches[0]


def _validated_controller_rollout_record(ledger: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the single authoritative successful attempt for this incident."""

    if ledger.get("controller_update_required") is not True:
        return None
    raw_records = ledger.get("controller_update_rollouts")
    if not isinstance(raw_records, list) or not raw_records:
        raise ControllerInstallError("controller rollout ledger is missing durable attempt records")
    incident_id = ledger.get("incident_id")
    reflection_commit = ledger.get("reflection_commit")
    before = ledger.get("controller_update_before_fingerprint")
    after = ledger.get("controller_update_after_fingerprint")
    if not all(isinstance(value, str) and value for value in (incident_id, reflection_commit, before, after)):
        raise ControllerInstallError("controller rollout ledger is missing its expected detector transition")
    successful: list[ControllerRolloutRecord] = []
    for raw_record in raw_records:
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
        returncodes = [record.get("returncode") for record in raw_records if isinstance(record, dict)]
        raise ControllerInstallError(f"durable controller rollout has no returncode=0 attempt: {returncodes}")
    if len(successful) != 1:
        raise ControllerInstallError("durable controller rollout requires exactly one successful attempt")
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
